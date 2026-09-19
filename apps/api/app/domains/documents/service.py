"""文档域服务：简历导入命令（校验、组织级去重、档案落库、Outbox 事件）。"""
import hashlib
import json

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.domains.documents.validation import FileValidationError, safe_filename, validate_file
from app.domains.tasks.idempotency import begin, complete
from app.domains.tasks.outbox import OutboxService
from app.domains.tasks.service import TaskService
from app.infrastructure.files.store import FileStore
from app.infrastructure.models import Candidate, CandidateFile, IdempotencyRecord, Job, ResumeVersion, Task


class ImportResult:
    def __init__(self, *, candidate_id: str, task_id: str, file_name: str, reused: bool):
        self.candidate_id = candidate_id
        self.task_id = task_id
        self.file_name = file_name
        self.reused = reused


def import_resume_files(
    db: Session,
    *,
    org_id: str,
    actor_id: str | None,
    job_id: str,
    files: list[tuple[str, bytes, str]],  # (filename, content, content_type)
    idempotency_key: str | None,
) -> list[ImportResult]:
    """导入一个或多个简历文件；业务写入与 Outbox 事件同事务（FR-032）。"""
    job = db.scalar(select(Job).where(Job.id == job_id, Job.org_id == org_id))
    if job is None:
        raise FileValidationError("JOB_NOT_FOUND", "岗位不存在或不在当前组织")

    key = (idempotency_key or "").strip()
    if key:
        request_hash = hashlib.sha256(
            json.dumps([(name, len(content)) for name, content, _ in files], ensure_ascii=False).encode()
        ).hexdigest()
        begin(
            db,
            org_id=org_id,
            actor_id=actor_id,
            operation="candidate.import",
            key=key,
            request_hash=request_hash,
        )

    results: list[ImportResult] = []
    store = FileStore()
    task_service = TaskService(db)
    outbox = OutboxService(db)

    for filename, content, content_type in files:
        validation = validate_file(filename=filename, content=content, max_bytes=settings.max_upload_bytes)
        digest = hashlib.sha256(content).hexdigest()

        existing = db.scalar(
            select(CandidateFile).where(CandidateFile.org_id == org_id, CandidateFile.sha256 == digest)
        )
        if existing is not None:
            # 组织内重复文件：返回既有资源，不新建候选人（FR-004）
            task = db.scalar(
                select(Task)
                .where(
                    Task.org_id == org_id,
                    or_(Task.aggregate_id == existing.candidate_id, Task.candidate_id == existing.candidate_id),
                )
                .order_by(Task.created_at.desc())
            )
            if task is None:
                raise FileValidationError("TASK_NOT_FOUND", "去重命中但找不到任务")
            results.append(
                ImportResult(candidate_id=existing.candidate_id, task_id=task.id, file_name=filename, reused=True)
            )
            continue

        candidate = Candidate(org_id=org_id, job_id=job.id, name=safe_filename(filename, "待解析候选人"))
        db.add(candidate)
        db.flush()

        storage_key = store.store(category="resumes", suffix=validation.suffix, content=content)
        file_row = CandidateFile(
            org_id=org_id,
            candidate_id=candidate.id,
            original_name=safe_filename(filename),
            stored_path=store.path_for(storage_key).as_posix(),
            storage_key=storage_key,
            sha256=digest,
            content_type=content_type or "application/octet-stream",
        )
        db.add(file_row)
        db.flush()

        version = ResumeVersion(
            candidate_id=candidate.id,
            file_id=file_row.id,
            version_no=1,
            status="PENDING",
            parser_version="resume-parser-1",
            quality_json="{}",
        )
        db.add(version)
        db.flush()

        task = task_service.create(
            org_id=org_id,
            task_type="RESUME_PARSE",
            aggregate_type="candidate",
            aggregate_id=candidate.id,
        )
        outbox.enqueue(
            org_id=org_id,
            event_type="resume.parse.requested",
            aggregate_type="candidate",
            aggregate_id=candidate.id,
            payload={
                "task_id": task.id,
                "file_id": file_row.id,
                "resume_version_id": version.id,
            },
        )
        results.append(
            ImportResult(candidate_id=candidate.id, task_id=task.id, file_name=filename, reused=False)
        )

    if key:
        record = db.scalar(
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.org_id == org_id,
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == "candidate.import",
                IdempotencyRecord.key == key,
            )
            .order_by(IdempotencyRecord.created_at.desc())
        )
        if record is not None:
            complete(
                db,
                record,
                response_status=202,
                response_json={"imports": [result.__dict__ for result in results]},
            )

    db.commit()
    return results
