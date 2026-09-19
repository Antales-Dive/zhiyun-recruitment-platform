"""检索基础设施单元测试：分词、BM25 与 RRF 融合。"""
from app.domains.assistant.service import _rerank
from app.infrastructure.search.ranking import bm25_score, rrf_merge, tokenize


class TestTokenize:
    def test_mixed_chinese_english(self):
        tokens = tokenize("Python 后端开发 FastAPI")
        assert "python" in tokens
        assert "fastapi" in tokens
        assert "后" in tokens
        assert "端" in tokens

    def test_case_insensitive(self):
        assert "FASTAPI".casefold() in tokenize("FastAPI")


class TestBm25:
    def test_relevant_document_scores_higher(self):
        query = tokenize("试用期 请假 审批")
        relevant = "试用期员工请假需直属负责人审批，请假需提前申请。"
        irrelevant = "招聘预算由财务部门负责，与试用期无关。"

        avgdl = 10
        relevant_score = bm25_score(query, relevant, avgdl=avgdl)
        irrelevant_score = bm25_score(query, irrelevant, avgdl=avgdl)

        assert relevant_score > 0
        assert irrelevant_score < relevant_score

    def test_empty_query_scores_zero(self):
        assert bm25_score([], "任意内容", avgdl=5) == 0.0


class TestRrf:
    def test_fusion_ranks_common_docs_first(self):
        merged = rrf_merge(["b", "a", "c"], ["b", "d"])
        assert merged[0] == "b"  # 两个列表都排第一

    def test_empty_rankings(self):
        assert rrf_merge([], []) == []

    def test_deterministic_order(self):
        assert rrf_merge(["x", "y"], ["y"]) == rrf_merge(["x", "y"], ["y"])


class TestRerank:
    def test_scores_follow_merged_document_ids(self):
        from types import SimpleNamespace

        candidates = [
            SimpleNamespace(chunk=SimpleNamespace(id="a", content="文档 A")),
            SimpleNamespace(chunk=SimpleNamespace(id="b", content="文档 B")),
        ]

        class Reranker:
            def rerank(self, _query, documents):
                assert documents == ["文档 B", "文档 A"]
                return [0.9, 0.1]

        assert _rerank("问题", candidates, ["b", "a"], Reranker()) == ["b", "a"]
