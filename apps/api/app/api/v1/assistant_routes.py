"""助手路由：RAG 查询与查询记录（授权引用、拒答状态）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_principal
from app.api.envelope import success
from app.domains.assistant.service import run_rag_query
from app.domains.identity.principal import Principal
from app.infrastructure.ai.openai_compatible import OpenAICompatibleEmbedding
from app.infrastructure.ai.ports import UnconfiguredRerank
from app.infrastructure.db import get_db
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.models import AssistantQueryLog

router = APIRouter(prefix="/api/v1")


class AssistantQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


@router.post("/assistant/query")
def query_assistant(
    payload: AssistantQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    outcome = run_rag_query(
        db,
        question=payload.question,
        org_id=principal.org_id,
        role=principal.role,
        gateway=ModelGateway(),
        reranker=UnconfiguredRerank(),
        embedding=OpenAICompatibleEmbedding(),
    )
    return success(
        request,
        {
            "query_id": db.scalar(
                select(AssistantQueryLog.id)
                .where(AssistantQueryLog.retrieval_run_id == outcome.retrieval_run_id)
                .order_by(AssistantQueryLog.created_at.desc())
            ),
            "answer": outcome.answer,
            "reliable": outcome.reliable,
            "citations": outcome.citations,
            "error_code": outcome.error_code,
        },
    )


@router.get("/assistant/queries/{query_id}")
def get_assistant_query(
    query_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """查询记录：本人或审计角色可读（04 §4.6）。"""
    log = db.scalar(
        select(AssistantQueryLog).where(
            AssistantQueryLog.id == query_id,
            AssistantQueryLog.org_id == principal.org_id,
        )
    )
    if log is None:
        raise HTTPException(status_code=404, detail="QUERY_NOT_FOUND")
    if log.actor_role != principal.role and not principal.has_role("AUDITOR"):
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    return success(
        request,
        {
            "query_id": log.id,
            "question": log.question,
            "answer": log.answer,
            "reliable": log.reliable,
            "error_code": log.error_code,
            "retrieval_run_id": log.retrieval_run_id,
            "model_run_id": log.model_run_id,
            "created_at": log.created_at.isoformat(),
        },
    )
