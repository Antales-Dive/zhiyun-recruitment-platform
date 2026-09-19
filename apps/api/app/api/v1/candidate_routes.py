"""候选人路由：简历导入（幂等、组织级去重）、候选人详情与简历版本。"""
import json

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_any_role, require_role
from app.api.envelope import success
from app.domains.documents.service import import_resume_files
from app.domains.documents.validation import FileValidationError
from app.domains.identity.principal import Principal
from app.domains.identity.service import can_access_candidate
from app.domains.tasks.idempotency import IdempotencyConflict
from app.infrastructure.db import get_db
from app.infrastructure.models import Candidate, ParsedProfile, ResumeBlock, ResumeVersion

router = APIRouter(prefix="/api/v1")


class ImportItemResponse(BaseModel):
    candidate_id: str
    task_id: str
    file_name: str
    reused: bool


@router.post("/candidates/import", status_code=status.HTTP_202_ACCEPTED)
async def import_candidates(
    job_id: str = Form(...),
    files: list[UploadFile] = File(...),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    contents = []
    for upload in files:
        raw = await upload.read()
        contents.append((upload.filename or "resume", raw, upload.content_type or "application/octet-stream"))

    try:
        results = import_resume_files(
            db,
            org_id=principal.org_id,
            actor_id=principal.user_id,
            job_id=job_id,
            files=contents,
            idempotency_key=idempotency_key,
        )
    except FileValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=_file_error_status(exc.code), detail=exc.code) from exc
    except IdempotencyConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc

    return success(
        request,
        {
            "imports": [
                ImportItemResponse(
                    candidate_id=item.candidate_id,
                    task_id=item.task_id,
                    file_name=item.file_name,
                    reused=item.reused,
                ).model_dump()
                for item in results
            ]
        },
    )


@router.get("/candidates/{candidate_id}")
def get_candidate_detail(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR", "INTERVIEWER")),
):
    candidate = db.scalar(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.org_id == principal.org_id)
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="CANDIDATE_NOT_FOUND")
    if not can_access_candidate(db, principal, candidate):
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    versions = list(
        db.scalars(
            select(ResumeVersion).where(ResumeVersion.candidate_id == candidate.id).order_by(ResumeVersion.version_no)
        ).all()
    )
    return success(
        request,
        {
            "candidate_id": candidate.id,
            "job_id": candidate.job_id,
            "name": candidate.name,
            "status": candidate.status,
            "resume_versions": [
                {
                    "resume_version_id": version.id,
                    "version_no": version.version_no,
                    "status": version.status,
                    "parser_version": version.parser_version,
                    "quality": json.loads(version.quality_json) if version.quality_json else {},
                }
                for version in versions
            ],
        },
    )


@router.get("/candidates/{candidate_id}/resumes/{resume_version_id}")
def get_resume_detail(
    candidate_id: str,
    resume_version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR", "INTERVIEWER")),
):
    version = db.scalar(
        select(ResumeVersion).join(Candidate, Candidate.id == ResumeVersion.candidate_id).where(
            ResumeVersion.id == resume_version_id,
            ResumeVersion.candidate_id == candidate_id,
            Candidate.org_id == principal.org_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="RESUME_NOT_FOUND")
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or not can_access_candidate(db, principal, candidate):
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    profile = db.scalar(select(ParsedProfile).where(ParsedProfile.resume_version_id == version.id))
    blocks = list(
        db.scalars(
            select(ResumeBlock).where(ResumeBlock.resume_version_id == version.id).order_by(ResumeBlock.ordinal)
        ).all()
    )
    return success(
        request,
        {
            "resume_version_id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "parser_version": version.parser_version,
            "profile": json.loads(profile.profile_json) if profile else None,
            "quality": json.loads(version.quality_json) if version.quality_json else {},
            "blocks": [
                {
                    "ordinal": block.ordinal,
                    "section": block.section,
                    "page_no": block.page_no,
                    "text": block.text,
                    "text_hash": block.text_hash,
                }
                for block in blocks
            ],
        },
    )


def _file_error_status(code: str) -> int:
    return {
        "FILE_TOO_LARGE": 413,
        "FILE_TYPE_UNSUPPORTED": 415,
        "INVALID_FILE_SIGNATURE": 415,
        "EMPTY_FILE": 422,
        "JOB_NOT_FOUND": 404,
        "TASK_NOT_FOUND": 404,
    }.get(code, 400)
