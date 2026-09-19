"""面试单元测试：状态机与令牌哈希。"""
import pytest
from app.domains.interviews.service import InterviewError, _token_hash
from app.infrastructure.models import InterviewSession


class TestSessionStateMachine:
    def test_token_hash_is_sha256(self):
        digest = _token_hash("raw-token-value")

        assert len(digest) == 64
        assert digest != "raw-token-value"
        assert _token_hash("raw-token-value") == digest  # 确定性

    def test_valid_statuses_cover_state_machine(self):
        expected = {"DRAFT", "INVITED", "IN_PROGRESS", "PAUSED", "TAKEN_OVER", "COMPLETED", "REVIEWED"}
        from app.domains.interviews.service import VALID_STATUSES

        assert VALID_STATUSES == expected

    def test_plan_persisted_with_session(self, db):
        session = InterviewSession(
            org_id="org-a",
            candidate_id="c1",
            job_version_id="jv1",
            resume_version_id="rv1",
            status="DRAFT",
            state_version=1,
            plan_json='{"questions": ["q1"], "max_rounds": 3}',
            coverage_json="{}",
        )
        db.add(session)
        db.commit()

        db.expire_all()
        stored = db.get(InterviewSession, session.id)
        assert stored.state_version == 1
        assert "max_rounds" in stored.plan_json

    def test_ai_reply_after_takeover_raises(self, db):
        from app.domains.interviews.service import append_ai_message

        session = InterviewSession(
            org_id="org-a",
            candidate_id="c1",
            job_version_id="jv1",
            resume_version_id="rv1",
            status="TAKEN_OVER",
            state_version=2,
            plan_json="{}",
            coverage_json="{}",
            taken_over_by="iv-1",
        )
        db.add(session)
        db.commit()

        with pytest.raises(InterviewError, match="SESSION_TAKEN_OVER"):
            append_ai_message(db, session, "不应发言")
