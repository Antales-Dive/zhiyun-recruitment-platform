"""问卷路由：管理端（HR）与候选人公开端（令牌）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import require_role
from app.api.envelope import success
from app.domains.identity.principal import Principal
from app.domains.questionnaires.service import (
    InvitationInvalidError,
    QuestionnaireError,
    create_invitation,
    create_questionnaire,
    public_questionnaire,
    submit_response,
)
from app.infrastructure.db import get_db

router = APIRouter(prefix="/api/v1")


class QuestionPayload(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: str = Field(pattern="^(single|multi|text)$")
    title: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=20)


class QuestionnaireCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    questions: list[QuestionPayload] = Field(min_length=1, max_length=50)
    scoring: dict = Field(default_factory=dict)


class InvitePayload(BaseModel):
    candidate_id: str
    expires_in_seconds: int = Field(default=7 * 24 * 3600, ge=300, le=90 * 24 * 3600)


@router.post("/questionnaires", status_code=201)
def create_questionnaire_route(
    payload: QuestionnaireCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    questionnaire = create_questionnaire(
        db,
        org_id=principal.org_id,
        title=payload.title,
        questions=[question.model_dump() for question in payload.questions],
        scoring=payload.scoring,
    )
    return success(
        request,
        {"questionnaire_id": questionnaire.id, "status": questionnaire.status},
    )


@router.post("/questionnaires/{questionnaire_id}/send", status_code=202)
def send_questionnaire_route(
    questionnaire_id: str,
    payload: InvitePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        invitation, token = create_invitation(
            db,
            org_id=principal.org_id,
            questionnaire_id=questionnaire_id,
            candidate_id=payload.candidate_id,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except QuestionnaireError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(
        request,
        {
            "invitation_id": invitation.id,
            "candidate_id": invitation.candidate_id,
            "token": token,
            "expires_at": invitation.expires_at.isoformat(),
        },
    )


public_router = APIRouter(prefix="/public")


@public_router.get("/questionnaires/{token}")
def get_public_questionnaire(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        data = public_questionnaire(db, token)
    except InvitationInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, data)


class SubmissionPayload(BaseModel):
    submission_id: str = Field(min_length=8, max_length=64)
    answers: dict


@public_router.post("/questionnaires/{token}/responses", status_code=201)
def submit_questionnaire_response(
    token: str,
    payload: SubmissionPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        response = submit_response(db, token=token, submission_id=payload.submission_id, answers=payload.answers)
    except InvitationInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(
        request,
        {
            "response_id": response.id,
            "submission_id": response.submission_id,
            "score": response.score_json,
        },
    )
