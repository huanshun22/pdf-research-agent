"""tools.py 单元测试：路径沙箱 + 三个工具的核心逻辑。"""

from __future__ import annotations

import pytest

from src.mcp_server import tools
from tests.conftest import make_pdf


class TestResolveSafePath:
    def test_normal_filename_resolves_inside_root(self, tmp_path):
        resolved = tools.resolve_safe_path(tmp_path, "paper.pdf")
        assert resolved == (tmp_path / "paper.pdf").resolve()

    def test_parent_traversal_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="越界"):
            tools.resolve_safe_path(tmp_path, "..\\evil.pdf")

    def test_absolute_path_rejected(self, tmp_path):
        outside = tmp_path.parent / "evil.pdf"
        with pytest.raises(ValueError, match="越界"):
            tools.resolve_safe_path(tmp_path, str(outside))


class TestListPdfs:
    def test_lists_all_pdfs_with_meta(self, sandbox_env):
        result = tools.list_pdfs()
        names = {p.filename for p in result.pdfs}
        assert names == {"rl_survey.pdf", "llm_agents.pdf", "broken.pdf"}
        assert result.total == 3
        assert str(sandbox_env) == result.root

        rl = next(p for p in result.pdfs if p.filename == "rl_survey.pdf")
        assert rl.pages == 3
        assert rl.error is None
        assert rl.size_bytes > 0

    def test_broken_pdf_reported_without_crash(self, sandbox_env):
        result = tools.list_pdfs()
        broken = next(p for p in result.pdfs if p.filename == "broken.pdf")
        assert broken.pages == 0
        assert broken.error is not None


class TestSearchPdfs:
    def test_case_insensitive_hit_with_page_and_snippet(self, sandbox_env):
        result = tools.search_pdfs("reinforcement")
        assert result.total_hits == 1
        hit = result.hits[0]
        assert hit.filename == "rl_survey.pdf"
        assert hit.page == 1
        assert "Reinforcement" in hit.snippet

    def test_hit_in_second_document(self, sandbox_env):
        result = tools.search_pdfs("ReAct")
        assert result.total_hits == 1
        assert result.hits[0].filename == "llm_agents.pdf"
        assert result.hits[0].page == 2

    def test_no_hit_returns_empty(self, sandbox_env):
        result = tools.search_pdfs("quantum-teleportation")
        assert result.total_hits == 0
        assert result.hits == []

    def test_empty_query_rejected(self, sandbox_env):
        with pytest.raises(ValueError, match="query"):
            tools.search_pdfs("   ")


class TestReadPdf:
    def test_read_single_page_default(self, sandbox_env):
        result = tools.read_pdf("rl_survey.pdf")
        assert result.pages_read == [1]
        assert result.total_pages == 3
        assert "Reinforcement" in result.pages[1]

    def test_read_page_range_inclusive(self, sandbox_env):
        result = tools.read_pdf("rl_survey.pdf", start_page=2, end_page=3)
        assert result.pages_read == [2, 3]
        assert "Q-learning" in result.pages[2]
        assert "policy gradient" in result.pages[3]

    def test_end_page_clamped_to_total(self, sandbox_env):
        result = tools.read_pdf("rl_survey.pdf", start_page=3, end_page=99)
        assert result.pages_read == [3]
        assert result.total_pages == 3

    def test_start_page_must_be_positive(self, sandbox_env):
        with pytest.raises(ValueError, match="start_page"):
            tools.read_pdf("rl_survey.pdf", start_page=0)

    def test_end_before_start_rejected(self, sandbox_env):
        with pytest.raises(ValueError, match="end_page"):
            tools.read_pdf("rl_survey.pdf", start_page=3, end_page=1)

    def test_page_cap_guard(self, sandbox_env):
        make_pdf(sandbox_env / "big.pdf", [f"page {i} filler text" for i in range(1, 26)])
        with pytest.raises(ValueError, match="最多读取"):
            tools.read_pdf("big.pdf", start_page=1, end_page=25)

    def test_missing_file_raises(self, sandbox_env):
        with pytest.raises(FileNotFoundError):
            tools.read_pdf("no_such.pdf")

    def test_path_traversal_rejected(self, sandbox_env):
        with pytest.raises(ValueError, match="越界"):
            tools.read_pdf("..\\secret.pdf")
