"""简历解析 Worker：消费 resume.parse.requested 事件。

处理流程：加载任务/文件/版本 → 解析（含 OCR 判定）→ 结构化档案与
质量门 → 落库 resume_blocks/parsed_profiles；质量不足或需 OCR 时
进入 NEEDS_REVIEW，不产生正常匹配事件（AC-003）。

事务边界：任务尝试与失败状态各自独立提交（崩溃可恢复）；
最终成功写入与解析结果同事务。
"""
from pathlib import Path

from sqlalchemy.orm import Session

from app.domains.documents.parsers import OcrRequiredError, parse_resume
from app.domains.documents.pipeline import assess_quality, block_digest, extract_profile
from app.domains.tasks.errors import PermanentTaskError, RetryableTaskError
from app.domains.tasks.service import TaskService
from app.infrastructure.files.store import FileStore
from app.infrastructure.messaging.envelope import EventEnvelope
from app.infrastructure.models import (
    Candidate,
    CandidateFile,
    ParsedProfile,
    ResumeBlock,
    ResumeVersion,
)

PARSER_VERSION = "resume-parser-1"
QUALITY_PARSED_THRESHOLD = 0.6


def handle_resume_parse_requested(db: Session, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    task_id = payload.get("task_id")
    file_id = payload.get("file_id")
    resume_version_id = payload.get("resume_version_id")
    if not task_id or not file_id or not resume_version_id:
        raise PermanentTaskError("resume.parse.requested 缺少必需 payload 字段")

    tasks = TaskService(db)
    task = tasks.snapshot(task_id=task_id, org_id=envelope.org_id)
    if task is None:
        raise PermanentTaskError(f"任务不存在：{task_id}")
    attempt = tasks.start_attempt(task)
    tasks.update_progress(task, progress=20, status="PROCESSING")
    db.commit()

    file_row = db.get(CandidateFile, file_id)
    version = db.get(ResumeVersion, resume_version_id)
    if file_row is None or version is None:
        _fail(
            db,
            tasks,
            task,
            attempt,
            "PERMANENT",
            f"文件或简历版本不存在：{file_id}/{resume_version_id}",
            permanent=True,
        )
        raise PermanentTaskError(f"文件或简历版本不存在：{file_id}/{resume_version_id}")

    candidate = db.get(Candidate, version.candidate_id)
    if candidate is None:
        _fail(db, tasks, task, attempt, "PERMANENT", "简历版本缺少候选人", permanent=True)
        raise PermanentTaskError("简历版本缺少候选人")

    file_type = _detect_file_type(file_row)
    try:
        path = FileStore().path_for(file_row.storage_key) if file_row.storage_key else None
        if path is None:
            _fail(db, tasks, task, attempt, "PERMANENT", f"存储键缺失：{file_row.id}", permanent=True)
            raise PermanentTaskError(f"存储键缺失：{file_row.id}")
        # OCR 端口生产未配置时按 NEEDS_OCR 处理（PD-003）
        outcome = parse_resume(path, file_type, ocr=None)
        tasks.update_progress(task, progress=60)
        db.commit()

        if outcome.needs_ocr:
            version.status = "NEEDS_OCR"
            candidate.status = "NEEDS_REVIEW"
            tasks.mark_review(
                task,
                attempt=attempt,
                error_code="OCR_REQUIRED",
                error_summary="图片或扫描件需要 OCR，生产 OCR 未配置（PD-003）",
            )
            db.commit()
            return

        if outcome.error is not None:
            # 解析出结果但质量不足（如文本过短）：进入人工复核而非重试
            version.status = "NEEDS_REVIEW"
            candidate.status = "NEEDS_REVIEW"
            tasks.mark_review(
                task,
                attempt=attempt,
                error_code="LOW_QUALITY",
                error_summary=outcome.error,
            )
            db.commit()
            return

        profile = extract_profile(outcome.blocks)
        quality = assess_quality(profile, outcome.blocks, outcome.parser_type)
        version.quality_json = quality.model_dump_json()
        version.parser_version = PARSER_VERSION

        if quality.confidence < QUALITY_PARSED_THRESHOLD:
            version.status = "NEEDS_REVIEW"
            candidate.status = "NEEDS_REVIEW"
            tasks.mark_review(
                task,
                attempt=attempt,
                error_code="LOW_QUALITY",
                error_summary="；".join(quality.warnings) or "解析质量不足",
            )
            db.commit()
            return

        # 落库结构化档案与原文块（证据定位用）
        profile_row = ParsedProfile(
            resume_version_id=version.id, schema_version=1, profile_json=profile.model_dump_json()
        )
        db.add(profile_row)
        for ordinal, block in enumerate(outcome.blocks):
            db.add(
                ResumeBlock(
                    resume_version_id=version.id,
                    ordinal=ordinal,
                    section=block.section,
                    page_no=block.page_no,
                    text=block.text[:8000],
                    text_hash=block_digest(block.text),
                )
            )
        version.status = "PARSED"
        candidate.status = "PARSED"
        candidate.name = profile.name or candidate.name
        tasks.update_progress(task, progress=100)
        tasks.finish(task, attempt=attempt, succeeded=True)
        db.commit()
    except OcrRequiredError as exc:
        version.status = "NEEDS_OCR"
        candidate.status = "NEEDS_REVIEW"
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


def _detect_file_type(file_row: CandidateFile) -> str:
    """按存储键后缀判定解析类型；未知类型回退到 PDF 解析路径。"""
    if file_row.storage_key:
        suffix = Path(file_row.storage_key).suffix.casefold()
        if suffix in {".txt", ".md"}:
            return "txt"
        if suffix == ".docx":
            return "docx"
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".png", ".jpg", ".jpeg"}:
            return "png" if suffix == ".png" else "jpeg"
    return "pdf"
