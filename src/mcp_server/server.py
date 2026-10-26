"""MCP stdio server：把 tools.py 的三个函数暴露为 MCP 工具。

启动方式：
    mcp dev src/mcp_server/server.py    # 用 MCP Inspector 调试（需要 Node.js）
    python src/mcp_server/server.py     # stdio 直接运行（Phase 2 Agent 接入用这种）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许以脚本方式直接运行（mcp dev / python server.py）：
# 把项目根目录加进 sys.path，保证 `import src.*` 可用
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server import tools

mcp = MCPServer(
    "pdf-research",
    instructions="检索和阅读 PDF_ROOT 目录下的本地 PDF 文件。先 list 看有什么，再 search 定位，最后 read 按页读取。",
)


def _safe(fn, /, *args, **kwargs):
    """把预期业务错误（越界/不存在/参数非法）转为可读的 ToolError。

    MCP 2.x 会把其他异常包装成笼统的 UnexpectedToolError，
    丢失原始原因；ToolError 的消息则原样传给 LLM，便于其自我纠正。
    """
    try:
        return fn(*args, **kwargs)
    except (ValueError, FileNotFoundError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def list_pdfs() -> tools.ListPdfsResult:
    """列出 PDF_ROOT 下所有 PDF 的元信息（文件名、页数、大小）。"""
    return _safe(tools.list_pdfs)


@mcp.tool()
def search_pdfs(query: str, max_hits: int = 20) -> tools.SearchPdfsResult:
    """在所有 PDF 中做大小写不敏感的关键词检索。

    返回命中的文件名、1 起始页码和上下文片段（每个页面最多报告一次命中）。
    每次调用实时扫描，不使用索引。
    """
    return _safe(tools.search_pdfs, query, max_hits=max_hits)


@mcp.tool()
def read_pdf(filename: str, start_page: int = 1, end_page: int | None = None) -> tools.ReadPdfResult:
    """按 1 起始的页码范围（含首尾）读取指定 PDF 的文本内容。

    filename 必须是 PDF_ROOT 内的文件名；一次最多读 20 页。
    """
    return _safe(tools.read_pdf, filename, start_page=start_page, end_page=end_page)


if __name__ == "__main__":
    mcp.run()  # 默认 stdio transport
