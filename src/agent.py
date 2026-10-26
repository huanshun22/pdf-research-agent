"""ReAct Agent：OpenAI function calling 驱动的编排循环 + MCP client。

架构（对应课程作业核心）：
    用户问题 → Thought（LLM 决定下一步）→ Action（经 MCP 调用 PDF 工具）
    → Observation（工具结果回填）→ …… → Final Answer

护栏：
    1. 路径沙箱 —— 在 MCP Server 端强制执行（见 tools.resolve_safe_path）
    2. 工具调用次数上限 —— 在本循环中强制执行，超限后禁用工具、强制产出最终答案
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

# 单次对话的工具调用上限（护栏 2）
MAX_TOOL_CALLS = 6

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "system.md"
SERVER_PATH = PROJECT_ROOT / "src" / "mcp_server" / "server.py"

BUDGET_EXHAUSTED_MSG = "工具调用次数已达上限，请基于已有观察直接给出最终答案。"


def load_system_prompt() -> str:
    """从 prompts/system.md 加载系统提示（Agent 的核心资产）。"""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def mcp_tools_to_openai(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """把 MCP 的 tool 列表（name/description/input_schema）转成 OpenAI function calling 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in mcp_tools
    ]


class PdfResearchAgent:
    """单用途 Agent：用自然语言检索和阅读 PDF_ROOT 下的本地 PDF。"""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        pdf_root: str | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        verbose: bool = False,
        client: Any = None,
    ):
        """client 参数用于测试注入假 LLM；生产环境传 None 时自动创建 AsyncOpenAI。"""
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.client = client
        self.model = model or "gpt-4o-mini"
        self.max_tool_calls = max_tool_calls
        self.verbose = verbose
        if pdf_root is None:
            from src.mcp_server.tools import get_pdf_root

            pdf_root = str(get_pdf_root())
        self.pdf_root = str(Path(pdf_root).resolve())
        self.system_prompt = load_system_prompt()
        self.trace: list[dict[str, str]] = []

    # ------------------------------------------------------------------ 入口

    async def ask(self, question: str) -> str:
        """回答一个自然语言问题：启动 MCP Server，跑 ReAct 循环，返回最终答案。"""
        self.trace.clear()
        async with stdio_client(self._server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                mcp_tools = (await session.list_tools()).tools
                openai_tools = mcp_tools_to_openai(mcp_tools)
                return await self._react_loop(session, question, openai_tools)

    def _server_params(self) -> StdioServerParameters:
        """以子进程方式启动 MCP stdio server，PDF_ROOT 显式传入环境。"""
        env = {**get_default_environment(), "PDF_ROOT": self.pdf_root}
        return StdioServerParameters(
            command=sys.executable, args=[str(SERVER_PATH)], env=env
        )

    # -------------------------------------------------------------- ReAct 循环

    async def _react_loop(
        self, session: ClientSession, question: str, openai_tools: list[dict[str, Any]]
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        used = 0
        while True:
            # 预算耗尽后不再提供工具，强制 LLM 基于已有观察收尾
            allow_tools = used < self.max_tool_calls
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
            if allow_tools:
                kwargs["tools"] = openai_tools
            response = await self.client.chat.completions.create(**kwargs)
            message = response.choices[0].message

            if not message.tool_calls:
                return message.content or ""

            messages.append(self._assistant_message(message))
            for tool_call in message.tool_calls:
                if used < self.max_tool_calls:
                    observation = await self._execute_tool_call(session, tool_call)
                    used += 1
                else:
                    # 本轮 LLM 仍请求了工具但预算已尽：回填提示，保持消息序列合法
                    observation = BUDGET_EXHAUSTED_MSG
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": observation,
                    }
                )

    # ------------------------------------------------------------------ 工具执行

    async def _execute_tool_call(self, session: ClientSession, tool_call: Any) -> str:
        name = tool_call.function.name
        args_raw = tool_call.function.arguments or "{}"
        if self.verbose:
            print(f"[tool] {name}({args_raw})", file=sys.stderr)
        try:
            arguments = json.loads(args_raw)
        except json.JSONDecodeError as exc:
            return self._record_trace(name, args_raw, f"参数解析失败（需要 JSON 对象）：{exc}")
        try:
            result = await session.call_tool(name, arguments)
        except Exception as exc:
            return self._record_trace(name, args_raw, f"工具执行异常：{exc}")
        return self._record_trace(name, args_raw, self._result_to_text(result))

    def _record_trace(self, name: str, arguments: str, observation: str) -> str:
        """记录一次工具调用到 self.trace（供网页端展示轨迹），并原样返回观察文本。"""
        self.trace.append({"name": name, "arguments": arguments})
        return observation

    @staticmethod
    def _result_to_text(result: Any) -> str:
        """把 MCP 工具结果压成一段文本，供 LLM 阅读。"""
        text_parts = [
            block.text for block in (result.content or []) if hasattr(block, "text")
        ]
        text = "\n".join(text_parts).strip()
        if getattr(result, "is_error", False):
            return f"工具返回错误：{text}"
        return text or "(工具返回空结果)"

    # ------------------------------------------------------------------ 消息转换

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
        """把 LLM 返回的 assistant 消息（含 tool_calls）转成可回传的 dict。"""
        entry: dict[str, Any] = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ]
        return entry
