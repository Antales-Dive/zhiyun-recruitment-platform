"""面试路由：管理端创建/邀请/接管/完成，候选人公开端消息与 SSE。"""
import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_role
from app.api.envelope import success
from app.config import settings
from app.domains.identity.principal import Principal
from app.domains.identity.service import can_access_candidate
from app.domains.interviews.service import (
    InterviewError,
    StateConflictError,
    TokenInvalidError,
    append_ai_message,
    append_candidate_message,
    complete_session,
    create_invitation,
    create_session,
    resolve_token,
    review_report,
    takeover,
)
from app.domains.tasks import stream
from app.infrastructure.db import get_db
from app.infrastructure.model_gateway import ModelGateway, ProviderError
from app.infrastructure.models import (
    Candidate,
    InterviewMessage,
    InterviewReport,
    InterviewSession,
    ModelRun,
)
from app.orchestration.interview_graph import PROMPT_VERSION, build_next_action

router = APIRouter(prefix="/api/v1")
public_router = APIRouter(prefix="/public")


class PlanPayload(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=30)
    max_rounds: int = Field(default=8, ge=1, le=30)
    forbidden: list[str] = Field(default_factory=list)


class SessionCreatePayload(BaseModel):
    candidate_id: str
    job_version_id: str
    resume_version_id: str
    plan: PlanPayload


class InvitePayload(BaseModel):
    expires_in_seconds: int = Field(default=72 * 3600, ge=300, le=30 * 24 * 3600)


class TakeoverPayload(BaseModel):
    reason: str = ""
    expected_state_version: int | None = None


class CompletePayload(BaseModel):
    expected_state_version: int | None = None


class ReviewReportPayload(BaseModel):
    conclusion: str = Field(min_length=1, max_length=1000)
    revision: str | None = Field(default=None, max_length=1000)


@router.post("/interviews", status_code=201)
def create_interview_route(
    payload: SessionCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        session = create_session(
            db,
            org_id=principal.org_id,
            candidate_id=payload.candidate_id,
            job_version_id=payload.job_version_id,
            resume_version_id=payload.resume_version_id,
            plan=payload.plan.model_dump(),
        )
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(request, {"session_id": session.id, "status": session.status})


@router.post("/interviews/{session_id}/invite", status_code=202)
def invite_interview_route(
    session_id: str,
    payload: InvitePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        _, token = create_invitation(
            db, session_id=session_id, org_id=principal.org_id, expires_in_seconds=payload.expires_in_seconds
        )
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, {"token": token})


@router.post("/interviews/{session_id}/takeover")
def takeover_route(
    session_id: str,
    payload: TakeoverPayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("INTERVIEWER")),
):
    session = db.scalar(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.org_id == principal.org_id,
        )
    )
    candidate = db.get(Candidate, session.candidate_id) if session else None
    if session is None or candidate is None or not can_access_candidate(db, principal, candidate):
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    try:
        session = takeover(
            db,
            session_id=session_id,
            org_id=principal.org_id,
            interviewer_id=principal.user_id or "interviewer",
            reason=payload.reason or None,
            expected_state_version=payload.expected_state_version,
        )
    except StateConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, {"session_id": session.id, "status": session.status})


@router.post("/interviews/{session_id}/complete", status_code=202)
def complete_interview_route(
    session_id: str,
    payload: CompletePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("INTERVIEWER")),
):
    session_row = db.scalar(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.org_id == principal.org_id,
        )
    )
    candidate = db.get(Candidate, session_row.candidate_id) if session_row else None
    if session_row is None or candidate is None or not can_access_candidate(db, principal, candidate):
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    try:
        session = complete_session(
            db, session_id=session_id, org_id=principal.org_id, expected_state_version=payload.expected_state_version
        )
    except StateConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, {"session_id": session.id, "status": session.status})


@router.post("/interview-reports/{report_id}/review")
def review_interview_report_route(
    report_id: str,
    payload: ReviewReportPayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("INTERVIEWER")),
):
    report_row = db.execute(
        select(InterviewReport, Candidate)
        .join(InterviewSession, InterviewSession.id == InterviewReport.session_id)
        .join(Candidate, Candidate.id == InterviewSession.candidate_id)
        .where(InterviewReport.id == report_id, InterviewSession.org_id == principal.org_id)
    ).first()
    if report_row is None or not can_access_candidate(db, principal, report_row[1]):
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    report, _ = report_row
    try:
        reviewed = review_report(
            db,
            report_id=report_id,
            org_id=principal.org_id,
            reviewer_id=principal.user_id or "interviewer",
            conclusion=payload.conclusion.strip(),
            revision=payload.revision.strip() if payload.revision else None,
        )
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(
        request,
        {
            "report_id": reviewed.id,
            "status": reviewed.status,
            "report": json.loads(reviewed.report_json),
        },
    )
@public_router.get("/interviews/{token}")
def get_public_interview(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        session = resolve_token(db, token)
    except TokenInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(
        request,
        {
            "session_id": session.id,
            "status": session.status,
            "taken_over": session.taken_over_by is not None,
        },
    )


class MessagePayload(BaseModel):
    client_message_id: str = Field(min_length=8, max_length=64)
    content: str = Field(min_length=1, max_length=4000)


@public_router.post("/interviews/{token}/messages", status_code=202)
def post_interview_message(
    token: str,
    payload: MessagePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """候选人消息：先持久化（幂等），再尝试 AI 回复（FR-017）。"""
    try:
        message, is_new = append_candidate_message(
            db, token=token, client_message_id=payload.client_message_id, content=payload.content
        )
    except InterviewError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    session = resolve_token(db, token)
    ai_reply = None
    if is_new and session.taken_over_by is None:
        ai_reply = _generate_ai_reply(db, session)

    return success(
        request,
        {
            "message": {
                "sequence": message.sequence,
                "actor_type": message.actor_type,
                "content": message.content,
            },
            "ai_reply": ai_reply,
        },
    )


def _generate_ai_reply(db: Session, session: InterviewSession) -> dict | None:
    """调用面试图生成下一动作；Provider 缺失/失败时返回显式状态，不编造。"""
    messages = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session.id)
            .order_by(InterviewMessage.sequence)
        ).all()
    )
    plan = json.loads(session.plan_json or "{}")
    asked = [message.content for message in messages if message.actor_type == "ai"]
    transcript = [{"actor_type": m.actor_type, "content": m.content} for m in messages]
    try:
        action = build_next_action(
            ModelGateway(),
            plan=plan,
            questions_asked=asked,
            messages=transcript,
            round_number=len(asked) + 1,
            max_rounds=int(plan.get("max_rounds", 8)),
        )
    except ProviderError as exc:
        # ProviderError 恒有 code（ModelNotConfiguredError 是其子类），无需 getattr 兜底。
        # 失败也要留痕：runbook 依赖 model_runs.status=FAILED 告警，静默返回错误码会让
        # "模型整段时间不可用"在看板上不可见。
        _record_model_run(db, session, error_code=exc.code)
        return {"error_code": exc.code}
    # 先记 ModelRun 再落业务写：成功路径只 flush，随 append_ai_message / complete_session
    # 的提交一并落库，保证"有 AI 消息必有对应调用留痕"处在同一事务内。
    _record_model_run(db, session, error_code=None)
    if action["action"] == "wrap_up" or action.get("done"):
        session = complete_session(db, session_id=session.id, org_id=session.org_id)
        return {"action": "wrap_up", "content": action["content"]}
    ai_message = append_ai_message(db, session, action["content"])
    return {"action": action["action"], "content": ai_message.content, "sequence": ai_message.sequence}


def _record_model_run(db: Session, session: InterviewSession, *, error_code: str | None) -> None:
    """一次模型调用一条 ModelRun 元数据（provider/model/prompt_version/结果）。

    与 assistant、matching 侧一致：只记元数据，不记 Prompt 与回答正文，
    避免调用留痕变成候选人对话内容的第二份存储。
    latency_ms / token 未记：interview_graph 只回传动作字典，未回传 Provider 元数据。
    """
    db.add(
        ModelRun(
            org_id=session.org_id,
            purpose="interview",
            provider="openai-compatible",
            model=settings.model_chat_name or "chat",
            prompt_version=PROMPT_VERSION,
            status="SUCCEEDED" if error_code is None else "FAILED",
            error_code=error_code,
        )
    )
    if error_code is None:
        db.flush()
    else:
        # 失败路径没有其它数据库写入，必须自行提交，否则留痕随请求结束一起回滚。
        db.commit()


@public_router.get("/interviews/{token}/events")
async def interview_events(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    try:
        session = resolve_token(db, token)
    except TokenInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    after_sequence: int | None = None
    if last_event_id:
        try:
            after_sequence = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="INVALID_EVENT_ID") from None
        oldest = stream.oldest_sequence(db, org_id=session.org_id, stream_id=session.id)
        if oldest is not None and after_sequence < oldest:
            raise HTTPException(status_code=409, detail="EVENT_CURSOR_EXPIRED")

    events = stream.replay(db, org_id=session.org_id, stream_id=session.id, after_sequence=after_sequence)

    async def event_generator():
        for event in events:
            data = json.dumps(json.loads(event.data_json), ensure_ascii=False)
            yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
        while True:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(settings.sse_heartbeat_seconds)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
