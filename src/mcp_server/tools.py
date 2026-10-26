"""三个 PDF 工具的核心实现。

本模块只包含纯函数逻辑，不依赖 MCP 框架，方便直接用 pytest 测试；
server.py 负责把这些函数包装成 MCP 工具暴露出去。

核心安全机制：所有文件路径都经过 resolve_safe_path() 校验，
确保不越出 PDF_ROOT 沙箱目录。
"""

from __future__ import annotations

import os
from pathlib import Path

from pypdf import PdfReader

from src.mcp_server.schemas import (
    ListPdfsResult,
    PdfMeta,
    ReadPdfResult,
    SearchHit,
    SearchPdfsResult,
)

# 单次 read_pdf 调用最多读取的页数，防止把整本书塞进上下文
MAX_PAGES_PER_CALL = 20
# 检索片段在命中关键词前后各保留的字符数
SNIPPET_CONTEXT_CHARS = 80


def get_pdf_root() -> Path:
    """返回 PDF_ROOT 沙箱目录。

    优先读环境变量 PDF_ROOT；未设置时默认 <项目根>/data/pdfs。
    """
    env_root = os.environ.get("PDF_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return (Path(__file__).resolve().parents[2] / "data" / "pdfs").resolve()


def resolve_safe_path(root: Path, filename: str) -> Path:
    """把用户传入的文件名解析为 root 沙箱内的安全绝对路径。

    沙箱规则：解析后的真实路径必须仍在 root 之内，
    否则视为目录遍历（../ 或绝对路径注入），直接抛 ValueError。
    """
    root = root.resolve()
    # 拼接后 resolve()：即使 filename 带 ../ 或是绝对路径，也能还原出真实落点
    candidate = (root / filename).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"路径越界：{filename!r} 超出 PDF_ROOT 沙箱 {root}")
    return candidate


def _iter_pdfs(root: Path) -> list[Path]:
    """列出沙箱目录下（非递归）的全部 PDF 文件，按文件名排序。"""
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.pdf") if p.is_file())


def list_pdfs(root: Path | None = None) -> ListPdfsResult:
    """列出 PDF_ROOT 下所有 PDF 的元信息（文件名、页数、大小）。

    单个文件损坏不影响其余文件的列出，损坏文件以 error 字段标注。
    """
    root = (root or get_pdf_root()).resolve()
    metas: list[PdfMeta] = []
    for path in _iter_pdfs(root):
        size = path.stat().st_size
        try:
            pages = len(PdfReader(path).pages)
            error = None
        except Exception as exc:  # 损坏的 PDF 也要列出来，不能让它挡住其他文件
            pages, error = 0, f"无法解析：{exc}"
        metas.append(PdfMeta(filename=path.name, pages=pages, size_bytes=size, error=error))
    return ListPdfsResult(root=str(root), total=len(metas), pdfs=metas)


def search_pdfs(query: str, max_hits: int = 20, root: Path | None = None) -> SearchPdfsResult:
    """在所有 PDF 中做大小写不敏感的关键词检索。

    返回命中的文件名、页码和上下文片段；每个页面最多报告一次命中。
    每次调用实时扫描，不构建索引。
    """
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")
    if max_hits < 1:
        raise ValueError("max_hits 必须 >= 1")

    root = (root or get_pdf_root()).resolve()
    needle = query.casefold()
    hits: list[SearchHit] = []
    for path in _iter_pdfs(root):
        if len(hits) >= max_hits:
            break
        try:
            reader = PdfReader(path)
        except Exception:
            continue  # 解析不了的文件直接跳过
        for page_idx, page in enumerate(reader.pages, start=1):
            if len(hits) >= max_hits:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            pos = text.casefold().find(needle)
            if pos == -1:
                continue
            hits.append(
                SearchHit(
                    filename=path.name,
                    page=page_idx,
                    snippet=_make_snippet(text, pos, len(query)),
                )
            )
    return SearchPdfsResult(query=query, total_hits=len(hits), hits=hits)


def _make_snippet(text: str, pos: int, query_len: int) -> str:
    """截取命中位置前后的上下文片段，压缩换行等空白字符。"""
    start = max(0, pos - SNIPPET_CONTEXT_CHARS)
    end = min(len(text), pos + query_len + SNIPPET_CONTEXT_CHARS)
    fragment = " ".join(text[start:end].split())
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{fragment}{suffix}"


def read_pdf(
    filename: str, start_page: int = 1, end_page: int | None = None, root: Path | None = None
) -> ReadPdfResult:
    """按 1 起始的页码范围（含首尾）读取指定 PDF 的文本内容。

    - end_page 缺省时只读 start_page 一页
    - end_page 超出文档末尾时截断到最后一页
    - 单次最多读 MAX_PAGES_PER_CALL 页，超出直接拒绝
    - 页码从 1 开始（对外接口），PDF 内部 0 起始
    """
    root = (root or get_pdf_root()).resolve()
    path = resolve_safe_path(root, filename)
    if not path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{filename}")

    reader = PdfReader(path)
    total = len(reader.pages)
    if start_page < 1:
        raise ValueError(f"start_page 从 1 开始，收到 {start_page}")
    if end_page is None:
        end_page = start_page
    if end_page < start_page:
        raise ValueError(f"end_page({end_page}) 不能小于 start_page({start_page})")
    end_page = min(end_page, total)  # 超出文档末尾时截断
    if end_page - start_page + 1 > MAX_PAGES_PER_CALL:
        raise ValueError(
            f"一次最多读取 {MAX_PAGES_PER_CALL} 页，当前请求 {end_page - start_page + 1} 页，请缩小页码范围"
        )

    pages: dict[int, str] = {}
    for page_idx in range(start_page, end_page + 1):
        try:
            text = (reader.pages[page_idx - 1].extract_text() or "").strip()
        except Exception as exc:
            text = f"(第 {page_idx} 页文本提取失败：{exc})"
        if not text:
            text = "(本页无可提取文本)"
        pages[page_idx] = text
    return ReadPdfResult(
        filename=path.name, total_pages=total, pages_read=sorted(pages), pages=pages
    )
