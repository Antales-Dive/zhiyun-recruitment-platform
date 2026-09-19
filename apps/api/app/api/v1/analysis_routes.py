"""分析运行路由：触发分析（幂等）与查看结果（FR-007/FR-009）。"""
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_any_role, require_role
from app.api.envelope import success
from app.domains.identity.principal import Principal
from app.domains.identity.service import can_access_candidate
from app.domains.tasks.idempotency import IdempotencyConflict, begin, complete
from app.domains.tasks.outbox import OutboxService
from app.domains.tasks.service import TaskService
from app.infrastructure.db import get_db
from app.infrastructure.models import (
    AgentRun,
    AnalysisRun,
    Candidate,
    IdempotencyRecord,
    ResumeVersion,
)

router = APIRouter(prefix="/api/v1")


@router.post("/candidates/{candidate_id}/analysis-runs", status_code=202)
def create_analysis_run(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    candidate = db.scalar(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.org_id == principal.org_id)
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="CANDIDATE_NOT_FOUND")

    resume = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.candidate_id == candidate.id, ResumeVersion.status == "PARSED")
        .order_by(ResumeVersion.version_no.desc())
    )
    if resume is None:
        raise HTTPException(status_code=409, detail="RESUME_NOT_READY")

    from app.domains.recruitment.service import job_snapshot

    snapshot = job_snapshot(db, job_version_id=_active_job_version_id(db, candidate), org_id=principal.org_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="JOB_VERSION_NOT_READY")

    key = (idempotency_key or "").strip()
    if key:
        try:
            existing = begin(
                db,
                org_id=principal.org_id,
                actor_id=principal.user_id,
                operation="analysis.run",
                key=key,
                request_hash=candidate_id,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
        if existing is not None:
            return success(
                request,
                {"run_id": json.loads(existing.response_json).get("run_id"), "reused": True},
            )

    run = AnalysisRun(
        org_id=principal.org_id,
        candidate_id=candidate.id,
        job_version_id=snapshot["job_version"].id,
        resume_version_id=resume.id,
        rubric_version_id=snapshot["rubric_version"].id,
        status="PENDING",
    )
    db.add(run)
    db.flush()

    task = TaskService(db).create(
        org_id=principal.org_id,
        task_type="MATCHING",
        aggregate_type="candidate",
        aggregate_id=candidate.id,
    )
    OutboxService(db).enqueue(
        org_id=principal.org_id,
        event_type="analysis.requested",
        aggregate_type="candidate",
        aggregate_id=candidate.id,
        payload={"analysis_run_id": run.id, "task_id": task.id},
    )
    if key:
        record = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.org_id == principal.org_id,
                IdempotencyRecord.actor_id == principal.user_id,
                IdempotencyRecord.operation == "analysis.run",
                IdempotencyRecord.key == key,
            )
        )
        if record is not None:
            complete(
                db,
                record,
                response_status=202,
                response_json={"run_id": run.id},
            )
    db.commit()
    return success(request, {"run_id": run.id, "task_id": task.id, "reused": False})


@router.get("/analysis-runs/{run_id}")
def get_analysis_run(
    run_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR", "INTERVIEWER")),
):
    run = db.scalar(
        select(AnalysisRun).where(AnalysisRun.id == run_id, AnalysisRun.org_id == principal.org_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    candidate = db.get(Candidate, run.candidate_id)
    if candidate is None or not can_access_candidate(db, principal, candidate):
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    agents = list(
        db.scalars(
            select(AgentRun).where(AgentRun.analysis_run_id == run.id).order_by(AgentRun.agent_type)
        ).all()
    )
    return success(
        request,
        {
            "run_id": run.id,
            "candidate_id": run.candidate_id,
            "status": run.status,
            "total_score": run.total_score,
            "confidence": run.confidence,
            "route": run.route,
            "error_code": run.error_code,
            "prompt_version": run.prompt_version,
            "agents": [
                {
                    "agent_type": agent.agent_type,
                    "status": agent.status,
                    "score": agent.score,
                    "confidence": agent.confidence,
                    "evidence_refs": json.loads(agent.evidence_json or "[]"),
                    "reason_codes": json.loads(agent.reason_codes_json or "[]"),
                }
                for agent in agents
            ],
        },
    )


def _active_job_version_id(db: Session, candidate: Candidate) -> str:
    from app.infrastructure.models import Job

    job = db.get(Job, candidate.job_id)
    if job is None or job.active_version_id is None:
        raise HTTPException(status_code=409, detail="JOB_VERSION_NOT_READY")
    return job.active_version_id
