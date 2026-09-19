"""匹配流水线集成测试：分析触发、Provider 缺失复核、证据持久化与路由。"""
import json
import re
from uuid import uuid4

import pytest
from app.contracts.matching import RubricConfig
from app.domains.documents.service import import_resume_files
from app.domains.recruitment.service import create_job
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
from app.workers.resume_worker import handle_resume_parse_requested


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def valid_resume() -> bytes:
    return (
        f"姓名：王强{uuid4().hex[:4]}\n邮箱 wangqiang@example.com\n电话 13911112222\n"
        "技能：Python、FastAPI、PostgreSQL\n"
        "工作经历：5 年 Python 后端开发，负责 RAG 系统与 API 服务"
    ).encode()


def prepared_candidate(db):
    """创建岗位 → 导入简历 → 解析完成，返回候选人与版本。"""
    created = create_job(
        db,
        org_id="org-a",
        title="Python 后端工程师",
        description="负责 API 与 RAG 系统",
        requirements=["Python", "FastAPI", "RAG"],
        rubric=RubricConfig(
            dimensions={"basic": 0.2, "skills": 0.4, "experience": 0.4},
            thresholds={"interview": 80, "questionnaire": 60, "talent_pool": 40},
        ),
    )
    result = import_resume_files(
        db,
        org_id="org-a",
        actor_id="u1",
        job_id=created.job.id,
        files=[("wang.txt", valid_resume(), "text/plain")],
        idempotency_key=None,
    )[0]

    from app.infrastructure.models import ResumeVersion

    version = db.query(ResumeVersion).filter_by(candidate_id=result.candidate_id).one()
    envelope = parse_envelope(
        json.dumps(
            build_envelope(
                event_id="evt-parse",
                event_type="resume.parse.requested",
                aggregate_type="candidate",
                aggregate_id=result.candidate_id,
                org_id="org-a",
                trace_id="test",
                payload={"task_id": result.task_id, "file_id": version.file_id, "resume_version_id": version.id},
            )
        )
    )
    handle_resume_parse_requested(db, envelope)
    db.expire_all()
    return result.candidate_id, result.task_id, version.id, created.job.id


def run_analysis(db, candidate_id, task_id, resume_version_id, job_id, gateway_class=None):
    from app.domains.tasks.outbox import OutboxService
    from app.domains.tasks.service import TaskService
    from app.infrastructure.models import AnalysisRun, Job
    from app.workers import matching_consumer
    from sqlalchemy import select

    job = db.get(Job, job_id)
    run = AnalysisRun(
        org_id="org-a",
        candidate_id=candidate_id,
        job_version_id=job.active_version_id,
        resume_version_id=resume_version_id,
        rubric_version_id=db.scalar(
            select(models.RubricVersion).where(models.RubricVersion.job_version_id == job.active_version_id)
        ).id,
        status="PENDING",
    )
    db.add(run)
    db.flush()
    task = TaskService(db).create(
        org_id="org-a", task_type="MATCHING", aggregate_type="candidate", aggregate_id=candidate_id
    )
    OutboxService(db).enqueue(
        org_id="org-a",
        event_type="analysis.requested",
        aggregate_type="candidate",
        aggregate_id=candidate_id,
        payload={"analysis_run_id": run.id, "task_id": task.id},
    )
    db.commit()

    if gateway_class is not None:
        original = matching_consumer.ModelGateway
        matching_consumer.ModelGateway = gateway_class
    envelope = parse_envelope(
        json.dumps(
            build_envelope(
                event_id="evt-analysis",
                event_type="analysis.requested",
                aggregate_type="candidate",
                aggregate_id=candidate_id,
                org_id="org-a",
                trace_id="test",
                payload={"analysis_run_id": run.id, "task_id": task.id},
            )
        )
    )
    try:
        matching_consumer.handle_analysis_requested(db, envelope)
    finally:
        if gateway_class is not None:
            matching_consumer.ModelGateway = original
    db.expire_all()
    return run.id, task.id


class StubGateway:
    def __init__(self):
        pass

    def is_configured(self):
        return True

    def chat(self, messages, *, timeout_seconds=60):
        match = re.search(r"（(basic|skills|experience)）", messages[1]["content"])
        dimension = match.group(1) if match else "basic"
        payload = {
            "dimension": dimension,
            "raw_score": 85,
            "confidence": 0.9,
            "evidence_refs": ["block-1"],
            "missing_fields": [],
            "reason_codes": [],
        }
        return type("R", (), {"content": json.dumps(payload)})()


class TestMatchingPipeline:
    def test_provider_missing_enters_review_with_model_not_configured(self, db):
        candidate_id, task_id, resume_version_id, job_id = prepared_candidate(db)

        run_id, task_id = run_analysis(db, candidate_id, task_id, resume_version_id, job_id)

        from app.infrastructure.models import AnalysisRun, Candidate, Task

        run = db.get(AnalysisRun, run_id)
        task = db.get(Task, task_id)
        candidate = db.get(Candidate, candidate_id)
        assert run.status == "NEEDS_REVIEW"
        assert run.error_code == "MODEL_NOT_CONFIGURED"
        assert run.total_score is None
        assert task.status == "NEEDS_REVIEW"
        assert candidate.status == "NEEDS_REVIEW"

    def test_provider_success_persists_scores_and_routes(self, db, monkeypatch):
        candidate_id, task_id, resume_version_id, job_id = prepared_candidate(db)

        # 注入真实证据块 ID：先查简历块
        from app.infrastructure.models import ResumeBlock
        from sqlalchemy import select

        block = db.scalar(select(ResumeBlock).where(ResumeBlock.resume_version_id == resume_version_id))
        StubGateway.refs = [block.id]

        original_chat = StubGateway.chat

        def chat_with_real_refs(self, messages, *, timeout_seconds=60):
            match = re.search(r"（(basic|skills|experience)）", messages[1]["content"])
            dimension = match.group(1) if match else "basic"
            payload = {
                "dimension": dimension,
                "raw_score": 85,
                "confidence": 0.9,
                "evidence_refs": StubGateway.refs,
                "missing_fields": [],
                "reason_codes": [],
            }
            return type("R", (), {"content": json.dumps(payload)})()

        StubGateway.chat = chat_with_real_refs
        try:
            run_id, task_id = run_analysis(db, candidate_id, task_id, resume_version_id, job_id, StubGateway)
        finally:
            StubGateway.chat = original_chat

        from app.infrastructure.models import (
            AgentRun,
            AnalysisRun,
            Candidate,
            RoutingDecision,
            Task,
        )

        run = db.get(AnalysisRun, run_id)
        task = db.get(Task, task_id)
        candidate = db.get(Candidate, candidate_id)
        agents = list(db.scalars(select(AgentRun).where(AgentRun.analysis_run_id == run_id)).all())
        decisions = list(
            db.scalars(select(RoutingDecision).where(RoutingDecision.analysis_run_id == run_id)).all()
        )

        assert run.status == "SUCCEEDED"
        assert run.total_score == 85.0
        assert run.route == "INTERVIEW"
        assert task.status == "SUCCEEDED"
        assert candidate.status == "INTERVIEW"
        assert len(agents) == 3
        assert all(agent.status == "SUCCEEDED" for agent in agents)
        assert json.loads(agents[0].evidence_json)
        assert len(decisions) == 1
        assert decisions[0].from_status == "PARSED"
        assert decisions[0].to_status == "INTERVIEW"

    def test_low_confidence_forces_review(self, db, monkeypatch):
        candidate_id, task_id, resume_version_id, job_id = prepared_candidate(db)

        from app.infrastructure.models import ResumeBlock
        from sqlalchemy import select

        block = db.scalar(select(ResumeBlock).where(ResumeBlock.resume_version_id == resume_version_id))
        refs = [block.id]

        class LowConfidenceGateway:
            def is_configured(self):
                return True

            def chat(self, messages, *, timeout_seconds=60):
                match = re.search(r"（(basic|skills|experience)）", messages[1]["content"])
                dimension = match.group(1) if match else "basic"
                payload = {
                    "dimension": dimension,
                    "raw_score": 90,
                    "confidence": 0.1,
                    "evidence_refs": refs,
                    "missing_fields": ["更多证据"],
                    "reason_codes": [],
                }
                return type("R", (), {"content": json.dumps(payload)})()

        run_id, _ = run_analysis(db, candidate_id, task_id, resume_version_id, job_id, LowConfidenceGateway)

        from app.infrastructure.models import AnalysisRun

        run = db.get(AnalysisRun, run_id)
        assert run.status == "NEEDS_REVIEW"
        assert run.error_code == "LOW_CONFIDENCE"
