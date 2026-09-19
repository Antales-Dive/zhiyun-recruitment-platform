import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import Principal, require_role
from app.config import settings
from app.contracts import (
    KnowledgePublishResponse,
    KnowledgeTaskResponse,
    KnowledgeUploadResponse,
)
from app.domains.documents.validation import FileValidationError, validate_file
from app.domains.knowledge.version_service import KnowledgeStateError, VersionService
from app.domains.tasks.outbox import OutboxService
from app.domains.tasks.service import TaskService
from app.infrastructure.db import get_db
from app.infrastructure.files.store import FileStore
from app.infrastructure.models import (
    KnowledgeDocument,
    KnowledgeIngestionTask,
    KnowledgeVersion,
    Task,
)

router = APIRouter(prefix="/api/v1")
SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}


def envelope(data):
    return {"request_id": "local", "trace_id": "local", "data": data, "error": None}


def _parse_allowed_roles(value: str) -> list[str]:
    roles = list(dict.fromkeys(role.strip().upper() for role in value.split(",") if role.strip()))
    if not roles or any(role not in settings.allowed_roles for role in roles):
        raise HTTPException(status_code=422, detail="INVALID_ALLOWED_ROLES")
    return roles


def _file_error_status(code: str) -> int:
    return {
        "FILE_TOO_LARGE": 413,
        "FILE_TYPE_UNSUPPORTED": 415,
        "INVALID_FILE_SIGNATURE": 415,
        "EMPTY_FILE": 422,
    }.get(code, 400)


@router.post("/knowledge/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_knowledge_document(
    title: str = Form(..., min_length=1, max_length=255),
    allowed_roles: str = Form("HR,INTERVIEWER,AUDITOR"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
):
    original_name = file.filename or "document"
    content = await file.read(settings.max_upload_bytes + 1)
    try:
        validation = validate_file(
            filename=original_name,
            content=content,
            content_type=file.content_type,
            max_bytes=settings.max_upload_bytes,
        )
    except FileValidationError as exc:
        raise HTTPException(status_code=_file_error_status(exc.code), detail=exc.code) from exc
    roles = _parse_allowed_roles(allowed_roles)

    document = KnowledgeDocument(org_id=principal.org_id, title=title.strip())
    version_id = str(uuid4())
    stored_path = FileStore().store(category="knowledge", suffix=validation.suffix, content=content)
    version = KnowledgeVersion(
        id=version_id,
        document=document,
        version_number=1,
        original_name=original_name,
        stored_path=stored_path,
        content_type=file.content_type or "application/octet-stream",
        content_sha256=hashlib.sha256(content).hexdigest(),
        allowed_roles_json=json.dumps(roles),
    )
    db.add(document)
    db.flush()
    task = TaskService(db).create(
        org_id=principal.org_id,
        task_type="KNOWLEDGE_INGEST",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
    )
    OutboxService(db).enqueue(
        org_id=principal.org_id,
        event_type="knowledge.ingestion.requested",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
        payload={"task_id": task.id, "version_id": version.id},
    )
    db.commit()
    return envelope(
        KnowledgeUploadResponse(
            document_id=document.id,
            version_id=version.id,
            task_id=task.id,
            status=task.status,
        ).model_dump()
    )


@router.get("/knowledge/tasks/{task_id}")
def get_knowledge_task(
    task_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
):
    row = db.execute(
        select(Task, KnowledgeVersion, KnowledgeDocument)
        .join(KnowledgeVersion, Task.aggregate_id == KnowledgeVersion.id)
        .join(KnowledgeDocument, KnowledgeVersion.document_id == KnowledgeDocument.id)
        .where(
            Task.id == task_id,
            Task.task_type == "KNOWLEDGE_INGEST",
            KnowledgeDocument.org_id == principal.org_id,
        )
    ).first()
    if row:
        task, version, document = row
        return envelope(
            KnowledgeTaskResponse(
                task_id=task.id,
                document_id=document.id,
                version_id=version.id,
                status="DONE" if task.status == "SUCCEEDED" else task.status,
                progress=task.progress,
                last_error=task.last_error,
            ).model_dump()
        )

    # 兼容旧版本创建的 KnowledgeIngestionTask 记录。
    legacy_row = db.execute(
        select(KnowledgeIngestionTask, KnowledgeVersion, KnowledgeDocument)
        .join(KnowledgeVersion, KnowledgeIngestionTask.version_id == KnowledgeVersion.id)
        .join(KnowledgeDocument, KnowledgeVersion.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeIngestionTask.id == task_id,
            KnowledgeDocument.org_id == principal.org_id,
        )
    ).first()
    if not legacy_row:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_TASK_NOT_FOUND")
    task, version, document = legacy_row
    return envelope(
        KnowledgeTaskResponse(
            task_id=task.id,
            document_id=document.id,
            version_id=version.id,
            status=task.status,
            progress=task.progress,
            last_error=task.last_error,
        ).model_dump()
    )


@router.post("/knowledge/documents/{document_id}/publish")
def publish_knowledge_document(
    document_id: str,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
    version_id: str | None = Query(default=None),
    expected_version: int | None = Query(default=None),
):
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.org_id == principal.org_id,
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_DOCUMENT_NOT_FOUND")

    target_version_id = version_id
    if target_version_id is None:
        latest = db.scalar(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.document_id == document.id)
            .order_by(KnowledgeVersion.version_number.desc())
        )
        target_version_id = latest.id if latest else None
    if not target_version_id:
        raise HTTPException(status_code=409, detail="KNOWLEDGE_VERSION_NOT_READY")

    try:
        version = VersionService(db).publish(
            document=document,
            version_id=target_version_id,
            expected_row_version=expected_version,
            actor_id=principal.user_id,
            trace_id=request.state.trace_id,
        )
        db.commit()
    except KnowledgeStateError as exc:
        db.rollback()
        code = str(exc)
        raise HTTPException(status_code=409, detail=code) from exc
    return envelope(
        KnowledgePublishResponse(
            document_id=document.id,
            version_id=version.id,
            status=version.status,
        ).model_dump()
    )


@router.post("/knowledge/documents/{document_id}/versions", status_code=status.HTTP_202_ACCEPTED)
async def upload_knowledge_version(
    document_id: str,
    file: UploadFile = File(...),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
):
    """为既有文档新增不可变版本（FR-021）：旧版本永不覆盖。"""
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.org_id == principal.org_id,
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_DOCUMENT_NOT_FOUND")

    original_name = file.filename or "document"
    suffix = Path(original_name).suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="DOCUMENT_TYPE_UNSUPPORTED")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
    if not content:
        raise HTTPException(status_code=422, detail="EMPTY_FILE")

    store = FileStore()
    storage_key = store.store(category="knowledge", suffix=suffix, content=content)
    version = VersionService(db).create_version(
        document=document,
        content_sha256=hashlib.sha256(content).hexdigest(),
        original_name=original_name,
        stored_path=storage_key,
        content_type=file.content_type or "application/octet-stream",
        allowed_roles=["HR", "INTERVIEWER", "AUDITOR"],
        actor_id=principal.user_id,
        trace_id=request.state.trace_id,
    )
    task = TaskService(db).create(
        org_id=principal.org_id,
        task_type="KNOWLEDGE_INGEST",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
    )
    OutboxService(db).enqueue(
        org_id=principal.org_id,
        event_type="knowledge.ingestion.requested",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
        payload={"task_id": task.id, "version_id": version.id},
    )
    db.commit()
    return envelope(
        KnowledgeUploadResponse(
            document_id=document.id,
            version_id=version.id,
            task_id=task.id,
            status=version.status,
        ).model_dump()
    )


@router.post("/knowledge/versions/{version_id}/rebuild", status_code=status.HTTP_202_ACCEPTED)
def rebuild_knowledge_version(
    version_id: str,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
):
    """重建索引：新 index_version，失败不影响当前已发布版本（FR-024）。"""
    version = db.scalar(
        select(KnowledgeVersion).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeVersion.document_id).where(
            KnowledgeVersion.id == version_id,
            KnowledgeDocument.org_id == principal.org_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_VERSION_NOT_FOUND")
    task = TaskService(db).create(
        org_id=principal.org_id,
        task_type="KNOWLEDGE_INGEST",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
    )
    OutboxService(db).enqueue(
        org_id=principal.org_id,
        event_type="knowledge.ingestion.requested",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
        payload={"task_id": task.id, "version_id": version.id},
    )
    db.commit()
    return envelope(
        KnowledgeTaskResponse(
            task_id=task.id,
            document_id=version.document_id,
            version_id=version.id,
            status=task.status,
            progress=task.progress,
        ).model_dump()
    )


@router.post("/knowledge/versions/{version_id}/withdraw")
def withdraw_knowledge_version(
    version_id: str,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("ADMIN")),
    reason: str = Form(default=""),
):
    """撤回活动版本：立即不可检索，保留历史（FR-023）。"""
    version = db.scalar(
        select(KnowledgeVersion).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeVersion.document_id).where(
            KnowledgeVersion.id == version_id,
            KnowledgeDocument.org_id == principal.org_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_VERSION_NOT_FOUND")
    document = db.get(KnowledgeDocument, version.document_id)
    assert document is not None, "知识版本缺少文档"
    try:
        VersionService(db).withdraw(
            document=document,
            version_id=version.id,
            reason=reason.strip() or None,
            actor_id=principal.user_id,
            trace_id=request.state.trace_id,
        )
        db.commit()
    except KnowledgeStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(
        KnowledgePublishResponse(
            document_id=document.id,
            version_id=version.id,
            status=version.status,
        ).model_dump()
    )
