"""AI 面试 E2E：人工接管竞争与接管后 AI 停言（AC-010）。"""
import asyncio
import uuid

import httpx
import pytest
from app.domains.interviews.service import (
    InterviewError,
    StateConflictError,
    append_ai_message,
    append_candidate_message,
    create_invitation,
    create_session,
    takeover,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, Job, JobVersion, ResumeVersion, RubricVersion


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def prepare_session(db):
    org = f"org-{uuid.uuid4().hex[:6]}"
    job = Job(org_id=org, title="后端工程师", description="d", skills_json="[]")
    db.add(job)
    db.flush()
    candidate = Candidate(org_id=org, job_id=job.id, name="候选人")
    db.add(candidate)
    db.flush()
    resume = ResumeVersion(
        candidate_id=candidate.id, file_id="", version_no=1, status="PARSED",
        parser_version="resume-parser-1", quality_json="{}",
    )
    db.add(resume)
    db.flush()
    job_version = JobVersion(job_id=job.id, version_no=1, description="d", requirements_json="[]")
    db.add(job_version)
    db.flush()
    db.add(RubricVersion(job_version_id=job_version.id, version_no=1, dimensions_json="{}", thresholds_json="{}"))
    job.active_version_id = job_version.id
    db.commit()

    session = create_session(
        db,
        org_id=org,
        candidate_id=candidate.id,
        job_version_id=job_version.id,
        resume_version_id=resume.id,
        plan={"questions": ["q1"], "max_rounds": 2, "forbidden": []},
    )
    _, token = create_invitation(db, session_id=session.id, org_id=org)
    return session, token, org


class TestInterviewTakeover:
    def test_takeover_blocks_ai_reply(self, db):
        """接管后 AI 不得继续自动发言（AC-010）。"""
        session, token, org = prepare_session(db)
        append_candidate_message(db, token=token, client_message_id="msg-1", content="你好")
        db.commit()

        takeover(db, session_id=session.id, org_id=org, interviewer_id="iv-1", reason="人工接管")
        db.commit()

        with pytest.raises(InterviewError, match="SESSION_TAKEN_OVER"):
            append_ai_message(db, session, "AI 不应在接管后发言")

        from app.infrastructure.models import InterviewMessage

        ai_messages = (
            db.query(InterviewMessage).filter_by(session_id=session.id, actor_type="ai").count()
        )
        assert ai_messages == 0

    def test_concurrent_takeover_only_one_wins(self, db):
        """两个面试官同时接管：乐观锁（state_version）裁决，只有一个成功。"""
        session, token, org = prepare_session(db)
        append_candidate_message(db, token=token, client_message_id="msg-2", content="你好")
        db.commit()

        first = takeover(
            db, session_id=session.id, org_id=org, interviewer_id="iv-1",
            reason="接管", expected_state_version=1,
        )
        db.commit()
        assert first.taken_over_by == "iv-1"

        # 第二个接管携带过期版本号：必须 409 语义（VERSION_CONFLICT）
        with pytest.raises(StateConflictError, match="VERSION_CONFLICT"):
            takeover(
                db, session_id=session.id, org_id=org, interviewer_id="iv-2",
                reason="竞争", expected_state_version=1,
            )

        db.expire_all()
        from app.infrastructure.models import InterviewSession

        refreshed = db.get(InterviewSession, session.id)
        assert refreshed.taken_over_by == "iv-1"
        assert refreshed.status == "TAKEN_OVER"

    def test_takeover_api_conflict_returns_409(self, db):
        """API 层：重复接管返回 409 CONFLICT。"""
        session, token, org = prepare_session(db)

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post(
                    f"/api/v1/interviews/{session.id}/takeover",
                    json={"reason": "人工接管", "expected_state_version": 1},
                    headers={"X-User-Role": "INTERVIEWER", "X-Org-Id": org},
                )
                second = await client.post(
                    f"/api/v1/interviews/{session.id}/takeover",
                    json={"reason": "再次接管", "expected_state_version": 1},
                    headers={"X-User-Role": "INTERVIEWER", "X-Org-Id": org},
                )
                return first, second

        first, second = asyncio.run(run())

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "VERSION_CONFLICT"
