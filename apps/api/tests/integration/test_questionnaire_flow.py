"""问卷流程集成测试：版本、邀请令牌、幂等提交与确定性评分。"""
import asyncio
import json

import httpx
import pytest
from app.domains.questionnaires.service import (
    InvitationInvalidError,
    create_invitation,
    create_questionnaire,
    resolve_invitation,
    submit_response,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, Job, QuestionnaireInvitation, QuestionnaireResponse

QUESTIONS = [
    {"id": "q1", "type": "single", "title": "能否接受出差？", "options": ["是", "否"]},
    {"id": "q2", "type": "multi", "title": "熟悉哪些技术？", "options": ["Python", "Go", "Java"]},
    {"id": "q3", "type": "text", "title": "自我介绍"},
]
SCORING = {
    "weights": {"q1": 1.0, "q2": 1.0, "q3": 0.5},
    "correct_options": {"q1": "是", "q2": ["Python", "Go"]},
}


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def candidate(db):
    job = Job(org_id="org-a", title="后端工程师", description="d", skills_json="[]")
    db.add(job)
    db.flush()
    candidate = Candidate(org_id="org-a", job_id=job.id, name="问卷候选人")
    db.add(candidate)
    db.commit()
    return candidate


class TestQuestionnaireFlow:
    def test_create_questionnaire_with_immutable_version(self, db):
        questionnaire = create_questionnaire(db, org_id="org-a", title="技术初筛", questions=QUESTIONS, scoring=SCORING)

        from app.infrastructure.models import QuestionnaireVersion

        version = db.get(QuestionnaireVersion, questionnaire.active_version_id)
        assert version.version_no == 1
        assert json.loads(version.questions_json)[0]["id"] == "q1"

    def test_invitation_token_stores_only_hash(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="问卷", questions=QUESTIONS, scoring=SCORING)
        invitation, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )

        db.expire_all()
        stored = db.get(QuestionnaireInvitation, invitation.id)
        assert stored.token_hash != token
        assert token not in str(stored.__dict__)
        assert len(stored.token_hash) == 64

    def test_public_questionnaire_exposes_minimal_fields(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="公开问卷", questions=QUESTIONS, scoring=SCORING)
        _, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )

        from app.domains.questionnaires.service import public_questionnaire

        data = public_questionnaire(db, token)

        assert data["title"] == "公开问卷"
        assert len(data["questions"]) == 3
        assert "scoring" not in json.dumps(data)
        assert "candidate" not in json.dumps(data).casefold()

    def test_expired_invitation_is_rejected(self, db, candidate):
        from datetime import timedelta

        from app.infrastructure.models import now_utc

        questionnaire = create_questionnaire(db, org_id="org-a", title="过期问卷", questions=QUESTIONS, scoring=SCORING)
        invitation, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )
        invitation.expires_at = now_utc() - timedelta(seconds=10)
        db.commit()

        with pytest.raises(InvitationInvalidError):
            resolve_invitation(db, token)

    def test_submit_response_scores_deterministically(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="评分问卷", questions=QUESTIONS, scoring=SCORING)
        _, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )

        response = submit_response(
            db,
            token=token,
            submission_id="submission-001",
            answers={"q1": "是", "q2": ["Python", "Go"], "q3": "5 年后端经验"},
        )

        score = json.loads(response.score_json)
        assert score["percentage"] == 100.0

    def test_duplicate_submission_id_is_idempotent(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="幂等问卷", questions=QUESTIONS, scoring=SCORING)
        _, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )

        answers = {"q1": "是", "q2": ["Python"], "q3": "x"}
        first = submit_response(db, token=token, submission_id="sub-dup", answers=answers)
        second = submit_response(db, token=token, submission_id="sub-dup", answers=answers)

        assert first.id == second.id
        assert db.query(QuestionnaireResponse).filter_by(invitation_id=first.invitation_id).count() == 1

    def test_used_invitation_rejects_second_submission(self, db, candidate):
        questionnaire = create_questionnaire(
            db, org_id="org-a", title="一次性问卷", questions=QUESTIONS, scoring=SCORING
        )
        _, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )
        submit_response(
            db, token=token, submission_id="sub-1", answers={"q1": "是", "q2": ["Python"], "q3": "x"}
        )

        with pytest.raises(InvitationInvalidError):
            submit_response(
                db, token=token, submission_id="sub-2", answers={"q1": "是", "q2": ["Python"], "q3": "x"}
            )


class TestQuestionnaireApi:
    def test_public_submission_via_api(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="API 问卷", questions=QUESTIONS, scoring=SCORING)
        _, token = create_invitation(
            db, org_id="org-a", questionnaire_id=questionnaire.id, candidate_id=candidate.id
        )

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                get_response = await client.get(f"/public/questionnaires/{token}")
                post_response = await client.post(
                    f"/public/questionnaires/{token}/responses",
                    json={"submission_id": "api-sub-1", "answers": {"q1": "是", "q2": ["Python", "Go"], "q3": "你好"}},
                )
                return get_response, post_response

        get_response, post_response = asyncio.run(run())

        assert get_response.status_code == 200
        assert post_response.status_code == 201
        body = post_response.json()["data"]
        assert json.loads(body["score"])["percentage"] == 100.0

    def test_send_invitation_requires_hr(self, db, candidate):
        questionnaire = create_questionnaire(db, org_id="org-a", title="权限问卷", questions=QUESTIONS, scoring=SCORING)

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    f"/api/v1/questionnaires/{questionnaire.id}/send",
                    json={"candidate_id": candidate.id},
                    headers={"X-User-Role": "INTERVIEWER", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())

        assert response.status_code == 403
