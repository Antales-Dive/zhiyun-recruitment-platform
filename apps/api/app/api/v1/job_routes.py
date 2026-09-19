"""岗位路由：版本化岗位创建、列表与新增版本（FR-001/FR-002）。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_role
from app.api.envelope import success
from app.contracts.matching import RubricConfig
from app.domains.identity.principal import Principal
from app.domains.recruitment.service import JobNotFoundError, add_job_version, create_job
from app.infrastructure.db import get_db
from app.infrastructure.models import Job, JobVersion

router = APIRouter(prefix="/api/v1")


class RubricPayload(BaseModel):
    dimensions: dict[str, float] | None = None
    thresholds: dict[str, int] | None = None
    confidence_threshold: float | None = None


class JobCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    requirements: list[str] = Field(default_factory=list, max_length=50)
    rubric: RubricPayload | None = None


class JobVersionPayload(BaseModel):
    description: str = ""
    requirements: list[str] = Field(default_factory=list, max_length=50)


def _build_rubric(payload: RubricPayload | None) -> RubricConfig:
    if payload is None:
        return RubricConfig()
    return RubricConfig(
        dimensions=payload.dimensions or dict(RubricConfig().dimensions),
        thresholds=payload.thresholds or dict(RubricConfig().thresholds),
        confidence_threshold=payload.confidence_threshold or RubricConfig().confidence_threshold,
    )


def _serialize_job(job: Job, versions: list[JobVersion]) -> dict:
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "active_version_id": job.active_version_id,
        "versions": [
            {
                "job_version_id": version.id,
                "version_no": version.version_no,
                "description": version.description,
                "requirements": json.loads(version.requirements_json or "[]"),
            }
            for version in versions
        ],
    }


@router.post("/jobs", status_code=201)
def create_job_route(
    payload: JobCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        rubric = _build_rubric(payload.rubric)
        created = create_job(
            db,
            org_id=principal.org_id,
            title=payload.title,
            description=payload.description,
            requirements=payload.requirements,
            rubric=rubric,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="VALIDATION_ERROR") from exc
    return success(
        request,
        _serialize_job(created.job, [created.job_version]),
    )


@router.get("/jobs")
def list_jobs_route(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    jobs = db.scalars(
        select(Job).where(Job.org_id == principal.org_id).order_by(Job.created_at.desc())
    ).all()
    items = []
    for job in jobs:
        versions = list(
            db.scalars(
                select(JobVersion).where(JobVersion.job_id == job.id).order_by(JobVersion.version_no)
            ).all()
        )
        items.append(_serialize_job(job, versions))
    return success(request, items)


@router.post("/jobs/{job_id}/versions", status_code=201)
def create_job_version_route(
    job_id: str,
    payload: JobVersionPayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        version = add_job_version(
            db,
            org_id=principal.org_id,
            job_id=job_id,
            description=payload.description,
            requirements=payload.requirements,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND") from exc
    return success(
        request,
        {
            "job_id": job_id,
            "job_version_id": version.id,
            "version_no": version.version_no,
        },
    )
