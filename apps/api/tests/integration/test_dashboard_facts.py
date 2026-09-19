"""仪表盘事实与日志脱敏测试（FR-034 / NFR-002）。"""
import logging

import pytest
from app.domains.analytics.service import dashboard_facts, metrics_text
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, Job, ModelRun, Task
from app.infrastructure.observability.logging import RedactingFormatter, redact_text


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
def org_data(db):
    from sqlalchemy import select

    job = db.scalar(select(Job).where(Job.org_id == "org-c"))
    if job is None:
        job = Job(org_id="org-c", title="岗位", description="d", skills_json="[]")
        db.add(job)
        db.flush()
    existing = {candidate.name: candidate for candidate in db.scalars(select(Candidate)).all()}
    created = []
    for name, status, org in (
        ("甲", "PARSED", "org-c"),
        ("乙", "INTERVIEW", "org-c"),
        ("丙", "NEEDS_REVIEW", "org-c"),
        ("丁", "PARSED", "org-b"),
    ):
        candidate = existing.get(name)
        if candidate is None:
            candidate = Candidate(org_id=org, job_id=job.id, name=name, status=status)
            db.add(candidate)
            created.append(candidate)
    if not db.scalar(select(Task).where(Task.org_id == "org-c")):
        anchor = created[0] if created else existing["甲"]
        db.add(Task(org_id="org-c", candidate_id=anchor.id, status="FAILED"))
    db.commit()
    return created or list(existing.values())


class TestDashboardFacts:
    def test_facts_are_org_scoped_and_db_derived(self, db, org_data):
        facts = dashboard_facts(db, org_id="org-c")

        assert facts["total"] == 3  # org-b 的候选人不可见
        assert facts["parsed"] == 1
        assert facts["needs_review"] == 1
        assert facts["failed"] == 1
        assert facts["routes"] == {}

    def test_metrics_text_contains_expected_lines(self, db, org_data):
        text = metrics_text(db, org_id="org-c")

        assert "zhiyun_candidates_total 3" in text
        assert "zhiyun_outbox_pending" in text
        assert "zhiyun_rag_refusal_rate" in text

    def test_model_errors_are_org_scoped(self, db, org_data):
        db.add_all([
            ModelRun(org_id="org-c", purpose="test", provider="test", model="test", status="FAILED"),
            ModelRun(org_id="org-b", purpose="test", provider="test", model="test", status="FAILED"),
        ])
        db.commit()

        text = metrics_text(db, org_id="org-c")

        assert "zhiyun_model_runs_failed 1" in text


class TestLogRedaction:
    def test_redacts_email_and_phone(self):
        message = "联系 hr@example.com 或 13800138000 处理任务"
        redacted = redact_text(message)

        assert "hr@example.com" not in redacted
        assert "13800138000" not in redacted
        assert "[REDACTED]" in redacted

    def test_redacts_bearer_tokens(self):
        message = "Authorization: Bearer abc.def.ghi 调用失败"
        redacted = redact_text(message)

        assert "abc.def.ghi" not in redacted

    def test_formatter_includes_structured_fields(self):
        formatter = RedactingFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="处理任务 contact=user@example.com", args=(), exc_info=None,
        )
        record.trace_id = "trace-1"
        record.task_id = "task-1"
        record.error_code = "TIMEOUT"

        formatted = formatter.format(record)

        assert "trace-1" in formatted
        assert "task-1" in formatted
        assert "TIMEOUT" in formatted
        assert "user@example.com" not in formatted

    def test_plain_text_does_not_leak_sensitive_markers(self, db, org_data):
        text = metrics_text(db, org_id="org-c")
        assert "example.com" not in text
