"""面试域：会话状态机、令牌、消息幂等、接管竞争与报告。"""
import hashlib
import json
import secrets
from datetime import timedelta

from app.domains.tasks import stream
from app.infrastructure.models import (
    Candidate,
    InterviewMessage,
    InterviewReport,
    InterviewSession,
    InterviewToken,
    ensure_utc,
    now_utc,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

VALID_STATUSES = {"DRAFT", "INVITED", "IN_PROGRESS", "PAUSED", "TAKEN_OVER", "COMPLETED", "REVIEWED"}


class InterviewError(ValueError):
    pass


class StateConflictError(InterviewError):
    pass


class TokenInvalidError(InterviewError):
    pass


class MessageConflictError(InterviewError):
    pass


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _append_message(
    db: Session,
    session: InterviewSession,
    *,
    actor_type: str,
    content: str,
    client_message_id: str | None = None,
) -> InterviewMessage:
    max_seq = db.scalar(
        select(func.max(InterviewMessage.sequence)).where(InterviewMessage.session_id == session.id)
    )
    message = InterviewMessage(
        session_id=session.id,
        sequence=(max_seq or 0) + 1,
        actor_type=actor_type,
        client_message_id=client_message_id,
        content=content,
    )
    db.add(message)
    db.flush()
    stream.append(
        db,
        org_id=session.org_id,
        stream_type="interview",
        stream_id=session.id,
        event_type="interview.message",
        data={"sequence": message.sequence, "actor_type": actor_type, "content": content},
    )
    return message


def create_session(
    db: Session,
    *,
    org_id: str,
    candidate_id: str,
    job_version_id: str,
    resume_version_id: str,
    plan: dict,
) -> InterviewSession:
    """创建面试会话（FR-016）：DRAFT 状态，题目计划与禁问配置随会话保存。"""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.org_id != org_id:
        raise InterviewError("CANDIDATE_NOT_FOUND")
    session = InterviewSession(
        org_id=org_id,
        candidate_id=candidate.id,
        job_version_id=job_version_id,
        resume_version_id=resume_version_id,
        status="DRAFT",
        state_version=1,
        plan_json=json.dumps(plan, ensure_ascii=False),
        coverage_json="{}",
    )
    db.add(session)
    db.commit()
    return session


def create_invitation(
    db: Session, *, session_id: str, org_id: str, expires_in_seconds: int = 72 * 3600
) -> tuple[InterviewToken, str]:
    """短期签名邀请：令牌只存哈希（FR-016）。"""
    session = db.scalar(
        select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.org_id == org_id)
    )
    if session is None:
        raise InterviewError("SESSION_NOT_FOUND")
    token = secrets.token_urlsafe(32)
    row = InterviewToken(
        session_id=session.id,
        token_hash=_token_hash(token),
        expires_at=now_utc() + timedelta(seconds=expires_in_seconds),
    )
    db.add(row)
    session.status = "INVITED"
    db.commit()
    return row, token


def resolve_token(db: Session, token: str) -> InterviewSession:
    row = db.scalar(select(InterviewToken).where(InterviewToken.token_hash == _token_hash(token)))
    if row is None:
        raise TokenInvalidError("TOKEN_NOT_FOUND")
    if row.revoked_at is not None or ensure_utc(row.expires_at) <= now_utc():
        raise TokenInvalidError("TOKEN_EXPIRED")
    session = db.get(InterviewSession, row.session_id)
    if session is None:
        raise TokenInvalidError("TOKEN_NOT_FOUND")
    return session


def append_candidate_message(
    db: Session, *, token: str, client_message_id: str, content: str
) -> tuple[InterviewMessage, bool]:
    """候选人消息：先持久化（幂等），再触发 AI 回复（FR-017）。"""
    if not content.strip():
        raise InterviewError("EMPTY_MESSAGE")
    session = resolve_token(db, token)
    if session.status not in {"INVITED", "IN_PROGRESS", "PAUSED"}:
        raise InterviewError("SESSION_NOT_ACTIVE")
    if session.taken_over_by is not None:
        raise InterviewError("SESSION_TAKEN_OVER")

    existing = db.scalar(
        select(InterviewMessage).where(
            InterviewMessage.session_id == session.id,
            InterviewMessage.client_message_id == client_message_id,
        )
    )
    if existing is not None:
        return existing, False

    message = _append_message(
        db, session, actor_type="candidate", content=content.strip()[:4000], client_message_id=client_message_id
    )
    if session.status == "INVITED":
        session.status = "IN_PROGRESS"
    db.commit()
    return message, True


def append_ai_message(db: Session, session: InterviewSession, content: str) -> InterviewMessage:
    """AI 回复：接管后不得继续自动发言（AC-010）；调用前再次校验接管状态。"""
    db.refresh(session)
    if session.taken_over_by is not None:
        raise InterviewError("SESSION_TAKEN_OVER")
    message = _append_message(db, session, actor_type="ai", content=content[:4000])
    db.commit()
    return message


def takeover(
    db: Session,
    *,
    session_id: str,
    org_id: str,
    interviewer_id: str,
    reason: str | None,
    expected_state_version: int | None = None,
) -> InterviewSession:
    """人工接管：乐观锁（state_version）裁决并发；接管后 AI 输出作废（AC-010）。"""
    session = db.scalar(
        select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.org_id == org_id)
    )
    if session is None:
        raise InterviewError("SESSION_NOT_FOUND")
    if expected_state_version is not None and session.state_version != expected_state_version:
        raise StateConflictError("VERSION_CONFLICT")
    if session.taken_over_by is not None:
        raise StateConflictError("ALREADY_TAKEN_OVER")
    session.taken_over_by = interviewer_id
    session.status = "TAKEN_OVER"
    session.state_version += 1
    stream.append(
        db,
        org_id=org_id,
        stream_type="interview",
        stream_id=session.id,
        event_type="interview.state_changed",
        data={"status": "TAKEN_OVER", "reason": reason or ""},
    )
    db.commit()
    return session


def complete_session(
    db: Session, *, session_id: str, org_id: str, expected_state_version: int | None = None
) -> InterviewSession:
    session = db.scalar(
        select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.org_id == org_id)
    )
    if session is None:
        raise InterviewError("SESSION_NOT_FOUND")
    if expected_state_version is not None and session.state_version != expected_state_version:
        raise StateConflictError("VERSION_CONFLICT")
    session.status = "COMPLETED"
    session.state_version += 1
    stream.append(
        db,
        org_id=org_id,
        stream_type="interview",
        stream_id=session.id,
        event_type="interview.state_changed",
        data={"status": "COMPLETED"},
    )
    create_report_draft(db, session_id=session.id, content="面试已完成，报告等待人工审核。")
    db.commit()
    return session


def review_report(
    db: Session,
    *,
    report_id: str,
    org_id: str,
    reviewer_id: str,
    conclusion: str,
    revision: str | None = None,
) -> InterviewReport:
    """面试官审核报告：REVIEWED 后才可进入招聘决策材料（FR-020）。"""
    report = db.scalar(
        select(InterviewReport).join(InterviewSession, InterviewSession.id == InterviewReport.session_id).where(
            InterviewReport.id == report_id,
            InterviewSession.org_id == org_id,
        )
    )
    if report is None:
        raise InterviewError("REPORT_NOT_FOUND")
    data = json.loads(report.report_json)
    data["conclusion"] = conclusion
    if revision:
        data["revision"] = revision
    report.report_json = json.dumps(data, ensure_ascii=False)
    report.status = "REVIEWED"
    report.reviewer_id = reviewer_id
    report.reviewed_at = now_utc()
    session = db.get(InterviewSession, report.session_id)
    if session is not None and session.status == "COMPLETED":
        session.status = "REVIEWED"
    db.commit()
    return report


def create_report_draft(db: Session, *, session_id: str, content: str) -> InterviewReport:
    """报告草稿：引用原消息内容，等待人工审核。"""
    existing = db.scalar(select(InterviewReport).where(InterviewReport.session_id == session_id))
    if existing is not None:
        return existing
    messages = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.sequence)
        ).all()
    )
    report = InterviewReport(
        session_id=session_id,
        status="DRAFT",
        schema_version=1,
        report_json=json.dumps(
            {
                "summary": content,
                "message_refs": [message.id for message in messages],
                "message_count": len(messages),
            },
            ensure_ascii=False,
        ),
    )
    db.add(report)
    db.commit()
    return report
