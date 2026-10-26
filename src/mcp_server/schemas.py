"""MCP 工具的输出数据模型（Pydantic）。

所有工具都返回结构化的 Pydantic 模型，
FastMCP 会自动把它们序列化为 MCP 的结构化内容。
"""

from pydantic import BaseModel, Field


class PdfMeta(BaseModel):
    """单个 PDF 文件的元信息。"""

    filename: str
    pages: int = Field(description="总页数；文件解析失败时为 0")
    size_bytes: int
    error: str | None = Field(default=None, description="解析失败原因，正常时为 None")


class ListPdfsResult(BaseModel):
    """list_pdfs 工具的返回值。"""

    root: str = Field(description="PDF_ROOT 沙箱目录的绝对路径")
    total: int = Field(description="PDF 文件数量")
    pdfs: list[PdfMeta]


class SearchHit(BaseModel):
    """一条检索命中记录。"""

    filename: str
    page: int = Field(description="命中的页码，从 1 开始")
    snippet: str = Field(description="命中关键词前后的上下文片段")


class SearchPdfsResult(BaseModel):
    """search_pdfs 工具的返回值。"""

    query: str
    total_hits: int = Field(description="命中条数（受 max_hits 截断）")
    hits: list[SearchHit]


class ReadPdfResult(BaseModel):
    """read_pdf 工具的返回值。"""

    filename: str
    total_pages: int = Field(description="文档总页数")
    pages_read: list[int] = Field(description="实际读取的页码列表（从 1 开始）")
    pages: dict[int, str] = Field(description="页码 -> 该页提取出的文本")
