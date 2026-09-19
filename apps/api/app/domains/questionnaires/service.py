"""问卷域：不可变版本、邀请令牌（哈希存储）、提交与确定性评分。"""
import hashlib
import json
import secrets
from datetime import timedelta

from app.domains.tasks.outbox import OutboxService
from app.infrastructure.models import (
    Candidate,
    Questionnaire,
    QuestionnaireInvitation,
    QuestionnaireResponse,
    QuestionnaireVersion,
    ensure_utc,
    now_utc,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


class QuestionnaireError(ValueError):
    pass


class InvitationInvalidError(QuestionnaireError):
    pass


class SubmissionConflictError(QuestionnaireError):
    pass


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_questionnaire(
    db: Session, *, org_id: str, title: str, questions: list[dict], scoring: dict
) -> Questionnaire:
    """创建问卷并生成不可变版本 1（FR-011）。"""
    questionnaire = Questionnaire(org_id=org_id, title=title.strip(), status="ACTIVE")
    db.add(questionnaire)
    db.flush()
    version = QuestionnaireVersion(
        questionnaire_id=questionnaire.id,
        version_no=1,
        questions_json=json.dumps(questions, ensure_ascii=False),
        scoring_json=json.dumps(scoring, ensure_ascii=False),
    )
    db.add(version)
    db.flush()
    questionnaire.active_version_id = version.id
    db.commit()
    return questionnaire


def create_invitation(
    db: Session,
    *,
    org_id: str,
    questionnaire_id: str,
    candidate_id: str,
    expires_in_seconds: int = 7 * 24 * 3600,
) -> tuple[QuestionnaireInvitation, str]:
    """创建一次性邀请：数据库只存令牌哈希，明文只在响应中出现一次（FR-012）。"""
    questionnaire = db.scalar(
        select(Questionnaire).where(
            Questionnaire.id == questionnaire_id, Questionnaire.org_id == org_id
        )
    )
    if questionnaire is None or questionnaire.active_version_id is None:
        raise QuestionnaireError("QUESTIONNAIRE_NOT_READY")
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.org_id != org_id:
        raise QuestionnaireError("CANDIDATE_NOT_FOUND")

    token = secrets.token_urlsafe(32)
    invitation = QuestionnaireInvitation(
        candidate_id=candidate.id,
        version_id=questionnaire.active_version_id,
        token_hash=_token_hash(token),
        status="PENDING",
        expires_at=now_utc() + timedelta(seconds=expires_in_seconds),
    )
    db.add(invitation)
    db.flush()

    OutboxService(db).enqueue(
        org_id=org_id,
        event_type="notification.requested",
        aggregate_type="questionnaire_invitation",
        aggregate_id=invitation.id,
        payload={
            "notification_id": f"q-{invitation.id}",
            "channel": "email",
            "template_version_id": "questionnaire-invite-v1",
            "recipient_ref": f"candidate:{candidate.id}",
        },
    )
    db.commit()
    return invitation, token


def resolve_invitation(db: Session, token: str) -> QuestionnaireInvitation:
    invitation = db.scalar(
        select(QuestionnaireInvitation).where(QuestionnaireInvitation.token_hash == _token_hash(token))
    )
    if invitation is None:
        raise InvitationInvalidError("INVITATION_NOT_FOUND")
    if invitation.status != "PENDING":
        raise InvitationInvalidError("INVITATION_USED")
    if ensure_utc(invitation.expires_at) <= now_utc():
        raise InvitationInvalidError("INVITATION_EXPIRED")
    return invitation


def public_questionnaire(db: Session, token: str) -> dict:
    """候选人视角的最小题目与到期信息（不暴露内部字段）。"""
    invitation = resolve_invitation(db, token)
    version = db.get(QuestionnaireVersion, invitation.version_id)
    assert version is not None, "问卷版本不存在"
    questionnaire = db.get(Questionnaire, version.questionnaire_id)
    assert questionnaire is not None, "问卷不存在"
    questions = json.loads(version.questions_json)
    public_questions = [
        {
            "id": question["id"],
            "type": question["type"],
            "title": question["title"],
            "options": question.get("options", []),
        }
        for question in questions
    ]
    return {
        "questionnaire_id": questionnaire.id,
        "title": questionnaire.title,
        "expires_at": invitation.expires_at.isoformat(),
        "questions": public_questions,
    }


def submit_response(
    db: Session, *, token: str, submission_id: str, answers: dict
) -> QuestionnaireResponse:
    """幂等提交：同邀请同 submission_id 返回既有结果（FR-013）。"""
    invitation = db.scalar(
        select(QuestionnaireInvitation).where(QuestionnaireInvitation.token_hash == _token_hash(token))
    )
    if invitation is None:
        raise InvitationInvalidError("INVITATION_NOT_FOUND")
    existing = db.scalar(
        select(QuestionnaireResponse).where(
            QuestionnaireResponse.invitation_id == invitation.id,
            QuestionnaireResponse.submission_id == submission_id,
        )
    )
    if existing is not None:
        return existing

    # 幂等未命中后才校验状态与过期
    invitation = resolve_invitation(db, token)

    version = db.get(QuestionnaireVersion, invitation.version_id)
    assert version is not None, "问卷版本不存在"
    score = _score_answers(version, answers)

    response = QuestionnaireResponse(
        invitation_id=invitation.id,
        submission_id=submission_id,
        answers_json=json.dumps(answers, ensure_ascii=False),
        score_json=json.dumps(score, ensure_ascii=False),
    )
    db.add(response)
    invitation.status = "SUBMITTED"
    db.commit()
    return response


def _score_answers(version: QuestionnaireVersion, answers: dict) -> dict:
    """确定性规则评分：每题权重与正确选项来自 scoring_json。"""
    questions = json.loads(version.questions_json)
    scoring = json.loads(version.scoring_json)
    weights = scoring.get("weights", {})
    total_weight = 0.0
    earned = 0.0
    details: dict[str, dict] = {}
    for question in questions:
        question_id = question["id"]
        weight = float(weights.get(question_id, 1.0))
        total_weight += weight
        answer = answers.get(question_id)
        correct = scoring.get("correct_options", {}).get(question_id)
        if question["type"] == "single" and answer is not None:
            hit = correct is not None and answer == correct
        elif question["type"] == "multi" and isinstance(answer, list):
            expected = set(correct or [])
            hit = expected and set(answer) == expected
        elif question["type"] == "text":
            hit = bool(str(answer or "").strip())
        else:
            hit = False
        if hit:
            earned += weight
        details[question_id] = {"weight": weight, "correct": hit}
    percentage = round(earned / total_weight * 100, 1) if total_weight else 0.0
    return {"percentage": percentage, "details": details}
