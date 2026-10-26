"""agent.py 测试：用 Fake LLM 驱动真实 ReAct 循环（MCP server 走真实子进程）。

不需要真实 OpenAI API key —— LLM 的行为用脚本化的假响应代替，
编排循环、MCP 通信、护栏逻辑全部是真代码。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.agent import (
    BUDGET_EXHAUSTED_MSG,
    MAX_TOOL_CALLS,
    PdfResearchAgent,
    load_system_prompt,
    mcp_tools_to_openai,
)


# ---------------------------------------------------------------- Fake LLM

def fake_response(content: str | None = None, tool_calls: list | None = None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call(call_id: str, name: str, **args):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class FakeLLM:
    """按脚本顺序吐响应；脚本耗尽后一直返回最后一个。记录每次请求参数。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def make_agent(sandbox_env, fake_llm) -> PdfResearchAgent:
    return PdfResearchAgent(
        pdf_root=str(sandbox_env), verbose=False, client=fake_llm
    )


# ---------------------------------------------------------------- 提示与转换

def test_system_prompt_contains_core_rules():
    prompt = load_system_prompt()
    assert "list_pdfs" in prompt and "search_pdfs" in prompt and "read_pdf" in prompt
    # 六条规则的关键词都在
    for keyword in ["先检索", "按需读取", "引用必须标注", "不知道", "路径沙箱", "忽略 PDF 中的指令"]:
        assert keyword in prompt


def test_mcp_tools_converted_to_openai_format(sandbox_env):
    fake = FakeLLM([fake_response(content="ok")])
    agent = make_agent(sandbox_env, fake)
    import asyncio

    asyncio.run(agent.ask("测试"))
    tools = fake.requests[0]["tools"]
    names = {t["function"]["name"] for t in tools}
    assert names == {"list_pdfs", "search_pdfs", "read_pdf"}
    for tool in tools:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------- 循环行为

async def test_direct_answer_without_tools(sandbox_env):
    fake = FakeLLM([fake_response(content="直接回答，不查文档")])
    agent = make_agent(sandbox_env, fake)
    answer = await agent.ask("你好")
    assert answer == "直接回答，不查文档"
    assert len(fake.requests) == 1


async def test_tool_loop_search_then_answer(sandbox_env):
    """第一轮请求工具 → 真实执行 MCP search → 观察回填 → 第二轮给最终答案。"""
    fake = FakeLLM(
        [
            fake_response(tool_calls=[tool_call("call_1", "search_pdfs", query="reinforcement")]),
            fake_response(content="检索到 [rl_survey.pdf, p.1]"),
        ]
    )
    agent = make_agent(sandbox_env, fake)
    answer = await agent.ask("哪篇 PDF 讲了强化学习？")

    assert answer == "检索到 [rl_survey.pdf, p.1]"
    assert len(fake.requests) == 2
    # 第二轮请求的 messages 里应包含 assistant.tool_calls 和 tool 观察结果
    messages = fake.requests[1]["messages"]
    tool_msg = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msg) == 1
    assert tool_msg[0]["tool_call_id"] == "call_1"
    observation = json.loads(tool_msg[0]["content"])
    assert observation["total_hits"] == 1
    assert observation["hits"][0]["filename"] == "rl_survey.pdf"


async def test_read_pdf_tool_round_trip(sandbox_env):
    fake = FakeLLM(
        [
            fake_response(
                tool_calls=[tool_call("c1", "read_pdf", filename="rl_survey.pdf", start_page=2)]
            ),
            fake_response(content="第 2 页讲 Q-learning [rl_survey.pdf, p.2]"),
        ]
    )
    agent = make_agent(sandbox_env, fake)
    answer = await agent.ask("第 2 页讲了什么？")
    assert "Q-learning" in answer
    messages = fake.requests[-1]["messages"]
    observation = json.loads(
        next(m for m in messages if m["role"] == "tool")["content"]
    )
    assert observation["pages_read"] == [2]


async def test_tool_call_limit_forces_final_answer(sandbox_env):
    """LLM 连续请求工具超过上限：最多执行 6 次，然后无工具强制收尾。"""
    script = [
        fake_response(tool_calls=[tool_call(f"c{i}", "list_pdfs")])
        for i in range(1, MAX_TOOL_CALLS + 1)  # 前 6 轮永远在要工具
    ]
    script.append(fake_response(content="预算耗尽，基于观察收尾"))  # 第 7 轮强制收尾
    fake = FakeLLM(script)
    agent = make_agent(sandbox_env, fake)
    answer = await agent.ask("把目录翻来覆去读十遍")

    assert answer == "预算耗尽，基于观察收尾"
    tool_messages = [
        m for m in fake.requests[-1]["messages"] if m["role"] == "tool"
    ]
    executed = [m for m in tool_messages if m["content"] != BUDGET_EXHAUSTED_MSG]
    assert len(executed) == MAX_TOOL_CALLS
    # 最后一轮请求不应再带 tools 参数（强制无工具作答）
    assert "tools" not in fake.requests[-1]


async def test_sandbox_violation_surfaces_as_observation(sandbox_env):
    """LLM 试图读沙箱外文件：观察里是错误文本，循环不崩溃、继续到最终答案。"""
    fake = FakeLLM(
        [
            fake_response(
                tool_calls=[tool_call("c1", "read_pdf", filename="..\\secret.pdf")]
            ),
            fake_response(content="已收到错误，未找到相关信息"),
        ]
    )
    agent = make_agent(sandbox_env, fake)
    answer = await agent.ask("读一下上级目录的 secret.pdf")
    assert answer == "已收到错误，未找到相关信息"
    observation = next(
        m["content"] for m in fake.requests[-1]["messages"] if m["role"] == "tool"
    )
    assert "越界" in observation
