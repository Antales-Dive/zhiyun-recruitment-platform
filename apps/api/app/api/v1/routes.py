
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_any_role
from app.api.envelope import success
from app.contracts import CandidateResponse
from app.domains.identity.principal import Principal
from app.domains.identity.service import candidate_scope_filter
from app.infrastructure.db import get_db
from app.infrastructure.models import Candidate, Task

router = APIRouter(prefix="/api/v1")


@router.get("/candidates")
def list_candidates(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("HR", "INTERVIEWER")),
):
    if principal.role == "AUDITOR":
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    scope = candidate_scope_filter(db, principal)
    if scope is False:
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    stmt = (
        select(Candidate)
        .where(Candidate.org_id == principal.org_id)
        .order_by(Candidate.created_at.desc())
    )
    if scope is not True:
        stmt = stmt.where(scope)
    candidates = db.scalars(stmt).all()
    result = []
    for candidate in candidates:
        task = db.scalar(
            select(Task).where(Task.candidate_id == candidate.id).order_by(Task.created_at.desc())
        )
        result.append(
            CandidateResponse(
                id=candidate.id,
                job_id=candidate.job_id,
                name=candidate.name,
                status=candidate.status,
                task_id=task.id if task else None,
                match_score=candidate.match_result.total_score if candidate.match_result else None,
                route=candidate.match_result.route if candidate.match_result else None,
            ).model_dump()
        )
    return success(request, result)
