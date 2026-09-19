"""检索基础设施：BM25 风格关键词、RRF 融合与证据门。"""
import re
from collections import Counter
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """中英文混合分词：连续字母数字串 + 单字 CJK。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        token = match.group(0)
        if token.isascii():
            tokens.append(token)
        else:
            tokens.extend(token)
    return tokens


@dataclass(frozen=True)
class ScoredDoc:
    doc_id: str
    score: float


def bm25_score(query_terms: list[str], document: str, *, avgdl: float, k1: float = 1.5, b: float = 0.75) -> float:
    """简化 BM25（单文档无 IDF 依赖全局统计时退化为 TF 加权）。"""
    if not query_terms:
        return 0.0
    terms = tokenize(document)
    if not terms:
        return 0.0
    dl = len(terms)
    freq = Counter(terms)
    score = 0.0
    for term in set(query_terms):
        tf = freq.get(term, 0)
        if tf == 0:
            continue
        score += (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avgdl, 1)))
    return score


def rrf_merge(*rankings: list[str], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion：按排名倒数求和融合多个检索结果。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
