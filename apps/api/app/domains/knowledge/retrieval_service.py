import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import KnowledgeChunk, KnowledgeDocument, KnowledgeVersion


@dataclass(frozen=True)
class Evidence:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    version: KnowledgeVersion
    score: float


def _term_groups(text: str) -> tuple[set[str], set[str]]:
    normalized = text.casefold()
    exact_terms = set(re.findall(r"[a-z0-9][a-z0-9_+.#-]*", normalized))
    chinese_terms: set[str] = set()
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        chinese_terms.update(
            sequence[index : index + 2] for index in range(max(1, len(sequence) - 1))
        )
    return exact_terms, {term for term in chinese_terms if term}


def _keyword_score(question: str, content: str) -> float:
    question_exact, question_chinese = _term_groups(question)
    content_exact, content_chinese = _term_groups(content)
    total_weight = len(question_chinese) + len(question_exact) * 8
    if not total_weight:
        return 0.0
    exact_overlap = question_exact & content_exact
    chinese_overlap = question_chinese & content_chinese
    if not exact_overlap and len(chinese_overlap) < min(2, len(question_chinese)):
        return 0.0
    overlap_weight = len(chinese_overlap) + len(exact_overlap) * 8
    return round(overlap_weight / total_weight, 4)


def retrieve_evidence(
    db: Session,
    *,
    question: str,
    org_id: str,
    role: str,
    limit: int = 5,
) -> list[Evidence]:
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeVersion, KnowledgeDocument)
        .join(KnowledgeVersion, KnowledgeChunk.version_id == KnowledgeVersion.id)
        .join(KnowledgeDocument, KnowledgeVersion.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.org_id == org_id,
            KnowledgeDocument.status == "PUBLISHED",
            KnowledgeVersion.status == "PUBLISHED",
        )
    ).all()
    ranked = []
    for chunk, version, document in rows:
        allowed_roles = set(json.loads(version.allowed_roles_json or "[]"))
        if role != "ADMIN" and role not in allowed_roles:
            continue
        score = _keyword_score(question, f"{chunk.section or ''}\n{chunk.content}")
        if score >= 0.15:
            ranked.append(Evidence(chunk=chunk, document=document, version=version, score=score))
    ranked = sorted(ranked, key=lambda item: (-item.score, item.chunk.ordinal))
    if not ranked:
        return []
    relative_cutoff = ranked[0].score * 0.75
    return [item for item in ranked if item.score >= relative_cutoff][:limit]
