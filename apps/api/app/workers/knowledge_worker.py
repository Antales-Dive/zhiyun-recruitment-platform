"""知识摄取 Worker：消费 knowledge.ingestion.requested。

流程：解析/OCR → 质量检查 → Parent/Child 分块 → 内容哈希去重 →
Index Manifest 数量校验并提交 → INDEXED。只有清单提交后的版本可发布。
"""
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.domains.knowledge.document_service import (
    OcrRequiredError,
    ParsedBlock,
    parse_document,
)
from app.domains.knowledge.version_service import IndexManifestService
from app.domains.tasks.errors import PermanentTaskError, RetryableTaskError
from app.domains.tasks.service import TaskService
from app.infrastructure.messaging.envelope import EventEnvelope
from app.infrastructure.models import KnowledgeChunk, KnowledgeVersion, Task

PARENT_CHUNK_MAX_CHARS = 6000
CHILD_CHUNK_MAX_CHARS = 800


def _estimate_tokens(text: str) -> int:
    """粗略 token 估计：中文字符按 1、其余按 4 字符/token；参数待评估冻结。"""
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk + (len(text) - cjk + 3) // 4


def parent_child_chunks(blocks: list[ParsedBlock]) -> list[tuple[ParsedBlock, list[ParsedBlock]]]:
    """Parent 保留完整上下文，Child 负责召回；同一父块内按字符上限切子块。"""
    pairs: list[tuple[ParsedBlock, list[ParsedBlock]]] = []
    for block in blocks:
        content = block.content.strip()
        if not content:
            continue
        parent = ParsedBlock(
            content=content[:PARENT_CHUNK_MAX_CHARS],
            section=block.section,
            page_number=block.page_number,
        )
        children: list[ParsedBlock] = []
        start = 0
        while start < len(content):
            children.append(
                ParsedBlock(
                    content=content[start : start + CHILD_CHUNK_MAX_CHARS],
                    section=block.section,
                    page_number=block.page_number,
                )
            )
            start += CHILD_CHUNK_MAX_CHARS
        pairs.append((parent, children))
    return pairs


def _write_chunks(db: Session, version: KnowledgeVersion, pairs) -> int:
    db.query(KnowledgeChunk).filter(KnowledgeChunk.version_id == version.id).delete()
    total = 0
    for parent, children in pairs:
        parent_row = KnowledgeChunk(
            version_id=version.id,
            chunk_type="parent",
            ordinal=total,
            section=parent.section,
            page_number=parent.page_number,
            content=parent.content,
            content_hash=hashlib.sha256(parent.content.encode("utf-8")).hexdigest(),
            token_count=_estimate_tokens(parent.content),
        )
        db.add(parent_row)
        db.flush()
        total += 1
        for child in children:
            db.add(
                KnowledgeChunk(
                    version_id=version.id,
                    parent_id=parent_row.id,
                    chunk_type="child",
                    ordinal=total,
                    section=child.section,
                    page_number=child.page_number,
                    content=child.content,
                    content_hash=hashlib.sha256(child.content.encode("utf-8")).hexdigest(),
                    token_count=_estimate_tokens(child.content),
                )
            )
            total += 1
    return total


def _resolve_stored_path(stored_key: str) -> Path:
    """兼容绝对路径（生产卷）与相对路径（本地开发，含旧版重复前缀）。"""
    from app.config import settings

    stored = Path(stored_key)
    if stored.is_absolute():
        return stored
    if str(stored).startswith(str(settings.upload_dir)):
        return stored
    return settings.upload_dir / stored


def handle_knowledge_ingestion_requested(db: Session, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    task_id = payload.get("task_id")
    version_id = payload.get("version_id")
    if not task_id or not version_id:
        raise PermanentTaskError("knowledge.ingestion.requested 缺少必需 payload 字段")

    tasks = TaskService(db)
    task = tasks.snapshot(task_id=task_id, org_id=envelope.org_id)
    if task is None:
        raise PermanentTaskError(f"任务不存在：{task_id}")
    attempt = tasks.start_attempt(task)
    tasks.update_progress(task, progress=10, status="PROCESSING")
    db.commit()

    version = db.get(KnowledgeVersion, version_id)
    if version is None:
        _fail(db, tasks, task, attempt, "PERMANENT", f"知识版本不存在：{version_id}", permanent=True)
        raise PermanentTaskError(f"知识版本不存在：{version_id}")

    try:
        path = _resolve_stored_path(version.stored_path)
        tasks.update_progress(task, progress=30)
        db.commit()

        parser_type, blocks = parse_document(path)
        if not blocks:
            _fail(db, tasks, task, attempt, "LOW_QUALITY", "文档未提取到有效内容")
            raise RetryableTaskError("文档未提取到有效内容")

        tasks.update_progress(task, progress=60)
        db.commit()

        # 内容哈希去重（版本内）
        seen: set[str] = set()
        unique_blocks: list[ParsedBlock] = []
        for block in blocks:
            digest = hashlib.sha256(block.content.encode("utf-8")).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique_blocks.append(block)

        pairs = parent_child_chunks(unique_blocks)
        if not pairs:
            _fail(db, tasks, task, attempt, "LOW_QUALITY", "分块结果为空")
            raise RetryableTaskError("分块结果为空")

        total = _write_chunks(db, version, pairs)
        # Index Manifest：lite 下 dense/sparse 均为落库块数，数量一致才提交
        manifest_service = IndexManifestService(db)
        manifest = manifest_service.begin(version.id)
        manifest_service.commit(manifest, dense_count=total, sparse_count=total)
        version.parser_type = parser_type
        version.status = "INDEXED"
        tasks.update_progress(task, progress=100)
        tasks.finish(task, attempt=attempt, succeeded=True)
        db.commit()
    except OcrRequiredError as exc:
        version.status = "NEEDS_OCR"
        tasks.mark_review(task, attempt=attempt, error_code="OCR_REQUIRED", error_summary=str(exc))
        db.commit()
    except (RetryableTaskError, PermanentTaskError):
        raise
    except Exception as exc:
        _fail(db, tasks, task, attempt, exc.__class__.__name__, str(exc))
        raise RetryableTaskError(str(exc)) from exc


def _fail(
    db: Session,
    tasks: TaskService,
    task,
    attempt,
    error_code: str,
    summary: str,
    *,
    permanent: bool = False,
) -> None:
    if permanent:
        tasks.fail_permanently(task, attempt=attempt, error_code=error_code, error_summary=summary)
    else:
        tasks.schedule_retry(task, attempt=attempt, error_code=error_code, error_summary=summary)
    db.commit()


def process_knowledge_task(task_id: str) -> None:
    """遗留入口：直接按旧 KnowledgeIngestionTask 处理（兼容既有测试/调用）。"""
    from app.infrastructure.db import SessionLocal
    from app.infrastructure.models import KnowledgeIngestionTask

    db = SessionLocal()
    try:
        ingestion = db.get(KnowledgeIngestionTask, task_id)
        if ingestion is None:
            task = db.get(Task, task_id)
            if task is not None and task.task_type == "KNOWLEDGE_INGEST" and task.aggregate_id:
                from app.infrastructure.messaging.envelope import build_envelope, parse_envelope

                envelope = parse_envelope(
                    json.dumps(
                        build_envelope(
                            event_id=f"legacy-{task.id}",
                            event_type="knowledge.ingestion.requested",
                            aggregate_type="knowledge_version",
                            aggregate_id=task.aggregate_id,
                            org_id=task.org_id,
                            trace_id="legacy",
                            payload={"task_id": task.id, "version_id": task.aggregate_id},
                        )
                    )
                )
                handle_knowledge_ingestion_requested(db, envelope)
            return
        if ingestion is None or ingestion.status not in {"PENDING", "RETRY"}:
            return
        ingestion.status = "PROCESSING"
        ingestion.progress = 10
        db.commit()

        version = db.get(KnowledgeVersion, ingestion.version_id)
        assert version is not None, "知识版本不存在"
        try:
            path = _resolve_stored_path(version.stored_path)
            parser_type, blocks = parse_document(path)
            pairs = parent_child_chunks(blocks)
            total = _write_chunks(db, version, pairs)
            manifest_service = IndexManifestService(db)
            manifest = manifest_service.begin(version.id)
            manifest_service.commit(manifest, dense_count=total, sparse_count=total)
            version.parser_type = parser_type
            version.status = "INDEXED"
            ingestion.status = "DONE"
            ingestion.progress = 100
            ingestion.last_error = None
            db.commit()
        except OcrRequiredError as exc:
            version.status = "NEEDS_OCR"
            ingestion.status = "NEEDS_OCR"
            ingestion.last_error = str(exc)
            db.commit()
        except Exception as exc:
            version.status = "FAILED"
            ingestion.status = "FAILED"
            ingestion.last_error = str(exc)[:1000]
            db.commit()
            raise
    finally:
        db.close()
