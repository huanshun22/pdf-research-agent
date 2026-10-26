r"""Phase 3 演示脚本：脚本化 Fake LLM，零 API key，一条命令跑完 4 个典型场景。

运行：
    .\.venv\Scripts\python.exe scripts\demo.py

说明：LLM 的"决策"由剧本预设，但编排循环、MCP 子进程、工具执行、
护栏逻辑全部是真实代码——演示的是系统的真实行为，不是录屏。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# 允许直接以脚本运行：把项目根目录加进 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import PdfResearchAgent
from src.mcp_server.tools import get_pdf_root


# ---------------------------------------------------------------- Fake LLM

def fake_response(content: str | None = None, tool_calls: list | None = None):
    """构造一帧 OpenAI 风格的响应。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call(call_id: str, name: str, **args):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class FakeLLM:
    """按剧本顺序吐响应，并记录每次请求，供演示打印工具轨迹。"""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


# ---------------------------------------------------------------- 轨迹打印

def _summarize_observation(content: str) -> str:
    """把一条 tool 观察压成一行摘要。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # 非 JSON：通常是错误文本（如沙箱拒绝），截断展示
        return content[:80] + ("…" if len(content) > 80 else "")
    if "total" in data:  # list_pdfs
        return f"{data['total']} 份 PDF"
    if "total_hits" in data:  # search_pdfs
        hits = data.get("hits", [])
        if hits:
            return f"命中 {data['total_hits']} 条 → {hits[0]['filename']} p.{hits[0]['page']}"
        return "命中 0 条"
    if "pages_read" in data:  # read_pdf
        return f"读取页 {data['pages_read']}（全文共 {data['total_pages']} 页）"
    return content[:80]


def print_trace(requests: list[dict]) -> None:
    """打印 ReAct 循环的工具轨迹。

    每个请求都携带完整历史，因此直接从最后一轮请求的 messages 里
    依次取 assistant.tool_calls（Action）与其后的 tool 消息（Observation）。
    """
    if not requests:
        return
    messages = requests[-1]["messages"]
    for idx, msg in enumerate(messages):
        if msg["role"] != "assistant" or not msg.get("tool_calls"):
            continue
        for tc in msg["tool_calls"]:
            print(f"  ↳ Action      → {tc['function']['name']}({tc['function']['arguments']})")
        # 观察是紧跟其后的 tool 消息
        for follow in messages[idx + 1:]:
            if follow["role"] != "tool":
                break
            print(f"    Observation → {_summarize_observation(follow['content'])}")


# ---------------------------------------------------------------- 场景编排

async def run_scene(title: str, question: str, fake_llm: FakeLLM) -> str:
    agent = PdfResearchAgent(
        pdf_root=str(get_pdf_root()),
        verbose=False,
        client=fake_llm,
    )
    print(f"\n{'─' * 64}")
    print(f"【场景】{title}")
    print(f"【问题】{question}")
    answer = await agent.ask(question)
    print_trace(fake_llm.requests)
    print(f"\n【回答】{answer}")
    return answer


async def main() -> None:
    # Windows 控制台默认 GBK，中文会乱码；强制 UTF-8 输出
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 64)
    print("  PDF Research Agent — Phase 3 演示")
    print("=" * 64)

    # 场景 1：完整链路 list → search → read → 带引用回答
    await run_scene(
        "完整链路：list → search → read → 带引用回答",
        "哪几篇 PDF 提到了深度学习？讲了什么？",
        FakeLLM([
            fake_response(tool_calls=[tool_call("c1", "list_pdfs")]),
            fake_response(tool_calls=[tool_call("c2", "search_pdfs", query="deep learning")]),
            fake_response(tool_calls=[tool_call(
                "c3", "read_pdf", filename="dl_notes.pdf", start_page=1, end_page=2)]),
            fake_response(content=(
                "《dl_notes.pdf》介绍了深度学习：第 1 页讲反向传播与梯度下降 [dl_notes.pdf, p.1]，"
                "第 2 页讲 Transformer 自注意力机制 [dl_notes.pdf, p.2]。")),
        ]),
    )

    # 场景 2：问题具体，直接 search，无需 list
    await run_scene(
        "精准检索：直接 search → 回答",
        "关于 policy gradient 的内容在哪篇 PDF 里？",
        FakeLLM([
            fake_response(tool_calls=[tool_call("c1", "search_pdfs", query="policy gradient")]),
            fake_response(content=(
                "《demo_rl_survey.pdf》第 3 页讲解了 policy gradient methods 和 actor critic "
                "[demo_rl_survey.pdf, p.3]。")),
        ]),
    )

    # 场景 3：检索无命中 → 诚实回复
    await run_scene(
        "诚实回复：检索无命中 → 明确说不知道",
        "有没有关于量子隐形传态的内容？",
        FakeLLM([
            fake_response(tool_calls=[tool_call("c1", "search_pdfs", query="quantum teleportation")]),
            fake_response(content="在提供的 PDF 中未找到相关信息。"),
        ]),
    )

    # 场景 4：路径越界 → 沙箱拒绝 → 安全收尾
    await run_scene(
        "护栏生效：路径越界 → 错误观察 → 安全收尾",
        "读一下上级目录的 secret.pdf",
        FakeLLM([
            fake_response(tool_calls=[tool_call("c1", "read_pdf", filename="..\\secret.pdf")]),
            fake_response(content=(
                "该请求越出了 PDF 沙箱目录，已被拒绝。"
                "我只能访问文档库内的文件；在提供的 PDF 中未找到相关内容。")),
        ]),
    )

    print(f"\n{'─' * 64}")
    print("演示结束。4 个场景均在真实 ReAct 循环 + 真实 MCP 工具上运行。")


if __name__ == "__main__":
    asyncio.run(main())
