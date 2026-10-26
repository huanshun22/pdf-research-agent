"""MCP Server 集成测试：以子进程方式启动 stdio server，用 MCP client 连接验证。

做的事和 mcp dev / MCP Inspector 一致：initialize -> list_tools -> call_tool。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="需要安装 mcp SDK")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "mcp_server" / "server.py"


def _server_params(pdf_root: Path) -> StdioServerParameters:
    """构造子进程启动参数：显式传 PDF_ROOT，其余用 SDK 的默认安全环境。"""
    env = {**get_default_environment(), "PDF_ROOT": str(pdf_root)}
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)], env=env)


def _payload(result) -> dict:
    """从 call_tool 结果中取出结构化数据（优先 structured_content，回退解析 JSON 文本）。"""
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured
    return json.loads(result.content[0].text)


async def test_list_tools_exposes_three_tools(sandbox_env):
    async with stdio_client(_server_params(sandbox_env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            names = {t.name for t in tools_result.tools}
            assert {"list_pdfs", "search_pdfs", "read_pdf"} <= names


async def test_call_list_pdfs_over_mcp(sandbox_env):
    async with stdio_client(_server_params(sandbox_env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_pdfs", {})
            assert not result.is_error
            data = _payload(result)
            names = {p["filename"] for p in data["pdfs"]}
            assert names == {"rl_survey.pdf", "llm_agents.pdf", "broken.pdf"}


async def test_call_search_pdfs_over_mcp(sandbox_env):
    async with stdio_client(_server_params(sandbox_env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_pdfs", {"query": "ReAct"})
            assert not result.is_error
            data = _payload(result)
            assert data["total_hits"] == 1
            assert data["hits"][0]["filename"] == "llm_agents.pdf"
            assert data["hits"][0]["page"] == 2


async def test_call_read_pdf_over_mcp(sandbox_env):
    async with stdio_client(_server_params(sandbox_env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "read_pdf", {"filename": "rl_survey.pdf", "start_page": 2, "end_page": 3}
            )
            assert not result.is_error
            data = _payload(result)
            assert data["pages_read"] == [2, 3]
            # structuredContent 保留 int 键，JSON 文本解析后是字符串键，两者都兼容
            pages = data["pages"]
            assert "Q-learning" in pages.get(2, pages.get("2", ""))


async def test_sandbox_violation_returns_tool_error(sandbox_env):
    async with stdio_client(_server_params(sandbox_env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("read_pdf", {"filename": "..\\evil.pdf"})
            assert result.is_error  # 越界请求以工具错误返回，而不是让 server 崩溃
