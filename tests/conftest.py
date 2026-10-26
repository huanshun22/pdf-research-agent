"""测试公共夹具：生成带指定文本的最小 PDF，以及临时沙箱目录。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)


def make_pdf(path: Path, page_texts: list[str]) -> None:
    """生成一个每页含一行文本的最小 PDF（Helvetica 字体 + Tj 指令）。

    pypdf 只能读不能写文本，这里手工构造内容流，足够 extract_text 使用。
    """
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        content = f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode("latin-1")
        stream = DecodedStreamObject()
        stream.set_data(content)
        stream[NameObject("/Length")] = NumberObject(len(content))
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    with open(path, "wb") as f:
        writer.write(f)


def _escape(text: str) -> str:
    """转义 PDF 字符串字面量中的特殊字符。"""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


@pytest.fixture
def sandbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """带 3 个示例 PDF 的临时沙箱目录，并设置 PDF_ROOT 环境变量指向它。"""
    root = tmp_path / "pdfs"
    root.mkdir()
    make_pdf(
        root / "rl_survey.pdf",
        [
            "Introduction to Reinforcement Learning survey",
            "Chapter 2 Q-learning algorithm details",
            "Chapter 3 policy gradient methods",
        ],
    )
    make_pdf(
        root / "llm_agents.pdf",
        [
            "Large Language Models are zero-shot reasoners",
            "ReAct: synergizing reasoning and acting in agents",
            "Appendix A prompt templates",
        ],
    )
    # 故意放一个损坏的 PDF，验证 list/search 的容错
    (root / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    monkeypatch.setenv("PDF_ROOT", str(root))
    return root
