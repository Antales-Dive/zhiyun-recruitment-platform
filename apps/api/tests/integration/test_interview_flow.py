"""AI 文字面试测试：状态机、消息幂等、禁问规则、接管竞争与 SSE 重连。"""
import json

import pytest
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
    takeover,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, InterviewMessage, InterviewSession, Job, ResumeVersion


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
def prepared(db):
    """岗位 + 候选人 + 简历版本（面试会话的外键依赖）。"""
    job = Job(org_id="org-a", title="后端工程师", description="d", skills_json="[]")
    db.add(job)
    db.flush()
    candidate = Candidate(org_id="org-a", job_id=job.id, name="面试候选人")
    db.add(candidate)
    db.flush()
    resume = ResumeVersion(
        candidate_id=candidate.id,
        file_id="",
        version_no=1,
        status="PARSED",
        parser_version="resume-parser-1",
        quality_json="{}",
    )
    db.add(resume)
    db.commit()

    from app.infrastructure.models import JobVersion, RubricVersion

    job_version = JobVersion(job_id=job.id, version_no=1, description="d", requirements_json="[]")
    db.add(job_version)
    db.flush()
    rubric = RubricVersion(job_version_id=job_version.id, version_no=1, dimensions_json="{}", thresholds_json="{}")
    db.add(rubric)
    job.active_version_id = job_version.id
    db.commit()
    return {
        "job_version_id": job_version.id,
        "resume_version_id": resume.id,
        "candidate_id": candidate.id,
    }


def make_session(db, prepared, **plan_overrides):
    plan = {
        "questions": ["请介绍你的后端经验", "如何处理线上事故？"],
        "max_rounds": 3,
        "forbidden": [],
    }
    plan.update(plan_overrides)
    return create_session(
        db,
        org_id="org-a",
        candidate_id=prepared["candidate_id"],
        job_version_id=prepared["job_version_id"],
        resume_version_id=prepared["resume_version_id"],
        plan=plan,
    )


class TestInterviewFlow:
    def test_create_and_invite(self, db, prepared):
        session = make_session(db, prepared)
        assert session.status == "DRAFT"

        token_row, token = create_invitation(db, session_id=session.id, org_id="org-a")
        db.expire_all()
        refreshed = db.get(InterviewSession, session.id)
        assert refreshed.status == "INVITED"
        assert token_row.token_hash != token

    def test_candidate_message_persists_then_status_in_progress(self, db, prepared):
        session = make_session(db, prepared)
        _, token = create_invitation(db, session_id=session.id, org_id="org-a")

        message, is_new = append_candidate_message(
            db, token=token, client_message_id="msg-0001", content="你好，我准备好了"
        )

        assert is_new is True
        assert message.actor_type == "candidate"
        db.expire_all()
        assert db.get(InterviewSession, session.id).status == "IN_PROGRESS"

    def test_duplicate_client_message_id_is_idempotent(self, db, prepared):
        session = make_session(db, prepared)
        _, token = create_invitation(db, session_id=session.id, org_id="org-a")

        first, is_new_first = append_candidate_message(
            db, token=token, client_message_id="msg-dup", content="第一次"
        )
        second, is_new_second = append_candidate_message(
            db, token=token, client_message_id="msg-dup", content="第一次"
        )

        assert is_new_first is True
        assert is_new_second is False
        assert first.id == second.id
        assert db.query(InterviewMessage).filter_by(session_id=session.id).count() == 1

    def test_takeover_blocks_ai_reply(self, db, prepared):
        session = make_session(db, prepared)
        _, token = create_invitation(db, session_id=session.id, org_id="org-a")
        append_candidate_message(db, token=token, client_message_id="msg-t", content="你好")

        takeover(db, session_id=session.id, org_id="org-a", interviewer_id="iv-1", reason="人工接管")

        with pytest.raises(InterviewError, match="SESSION_TAKEN_OVER"):
            append_ai_message(db, session, "AI 不应发言")

    def test_takeover_race_version_conflict(self, db, prepared):
        session = make_session(db, prepared)
        _, token = create_invitation(db, session_id=session.id, org_id="org-a")
        append_candidate_message(db, token=token, client_message_id="msg-race", content="你好")

        takeover(
            db, session_id=session.id, org_id="org-a", interviewer_id="iv-1", reason="接管", expected_state_version=1
        )
        with pytest.raises(StateConflictError):
            takeover(
                db,
                session_id=session.id,
                org_id="org-a",
                interviewer_id="iv-2",
                reason="竞争",
                expected_state_version=1,
            )

    def test_complete_then_review(self, db, prepared):
        session = make_session(db, prepared)
        _, token = create_invitation(db, session_id=session.id, org_id="org-a")
        append_candidate_message(db, token=token, client_message_id="msg-c", content="完成")

        completed = complete_session(db, session_id=session.id, org_id="org-a")
        assert completed.status == "COMPLETED"

        from app.domains.interviews.service import review_report
        from app.infrastructure.models import InterviewReport

        report = db.query(InterviewReport).filter_by(session_id=session.id).one()
        assert report.status == "DRAFT"
        assert json.loads(report.report_json)["message_refs"]

        reviewed = review_report(
            db, report_id=report.id, org_id="org-a", reviewer_id="iv-1", conclusion="通过"
        )
        assert reviewed.status == "REVIEWED"
        assert json.loads(reviewed.report_json)["conclusion"] == "通过"

    def test_expired_token_rejected(self, db, prepared):
        from datetime import timedelta

        from app.infrastructure.models import now_utc

        session = make_session(db, prepared)
        token_row, token = create_invitation(db, session_id=session.id, org_id="org-a")
        token_row.expires_at = now_utc() - timedelta(seconds=5)
        db.commit()

        with pytest.raises(TokenInvalidError):
            resolve_token(db, token)


class TestInterviewGraphGuard:
    def test_provider_missing_returns_explicit_error(self, db, prepared):
        from app.infrastructure.model_gateway import ModelGateway, ModelNotConfiguredError
        from app.orchestration.interview_graph import build_next_action

        with pytest.raises(ModelNotConfiguredError):
            build_next_action(
                ModelGateway(),
                plan={"questions": ["q1"]},
                questions_asked=[],
                messages=[],
                round_number=1,
                max_rounds=3,
            )

    def test_sensitive_attribute_question_rejected(self, db, prepared):
        from app.infrastructure.model_gateway import ProviderInvalidResponseError
        from app.orchestration.interview_graph import build_next_action

        class StubGateway:
            def is_configured(self):
                return True

            def chat(self, messages, *, timeout_seconds=60):
                payload = {"action": "question", "content": "请问你的年龄和婚育情况？", "done": False}
                return type("R", (), {"content": json.dumps(payload)})()

        with pytest.raises(ProviderInvalidResponseError, match="敏感属性"):
            build_next_action(
                StubGateway(),
                plan={"questions": ["q1"]},
                questions_asked=[],
                messages=[],
                round_number=1,
                max_rounds=3,
            )

    def test_wrap_up_when_rounds_exhausted(self, db, prepared):
        from app.orchestration.interview_graph import build_next_action

        class StubGateway:
            def is_configured(self):
                return True

            def chat(self, messages, *, timeout_seconds=60):
                payload = {"action": "question", "content": "还有问题", "done": False}
                return type("R", (), {"content": json.dumps(payload)})()

        action = build_next_action(
            StubGateway(),
            plan={"questions": ["q1"]},
            questions_asked=["q1", "q2", "q3"],
            messages=[],
            round_number=4,
            max_rounds=3,
        )
        assert action["action"] == "wrap_up"
        assert action["done"] is True
