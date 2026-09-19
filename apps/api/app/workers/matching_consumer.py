"""Matching Consumer：消费 analysis.requested，运行 LangGraph 并持久化证据。"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.matching import AnalysisSnapshot
from app.domains.matching.graph import DIMENSIONS, run_matching
from app.domains.recruitment.service import job_snapshot
from app.domains.tasks.errors import PermanentTaskError
from app.domains.tasks.service import TaskService
from app.infrastructure.messaging.envelope import EventEnvelope
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.models import (
    AgentRun,
    AnalysisRun,
    Candidate,
    ModelRun,
    ResumeBlock,
    RoutingDecision,
    Task,
)

PROMPT_VERSION = "matching-agents-v1"


def handle_analysis_requested(db: Session, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    analysis_run_id = payload.get("analysis_run_id")
    task_id = payload.get("task_id")
    if not analysis_run_id or not task_id:
        raise PermanentTaskError("analysis.requested 缺少必需 payload 字段")

    tasks = TaskService(db)
    task = tasks.snapshot(task_id=task_id, org_id=envelope.org_id)
    if task is None:
        raise PermanentTaskError(f"任务不存在：{task_id}")
    attempt = tasks.start_attempt(task)
    tasks.update_progress(task, progress=10, status="PROCESSING")
    db.commit()

    run = db.get(AnalysisRun, analysis_run_id)
    if run is None or run.org_id != envelope.org_id:
        _fail(db, tasks, task, attempt, "PERMANENT", f"分析运行不存在：{analysis_run_id}")
        raise PermanentTaskError(f"分析运行不存在：{analysis_run_id}")

    snapshot_data = job_snapshot(db, job_version_id=run.job_version_id, org_id=envelope.org_id)
    if snapshot_data is None:
        _fail(db, tasks, task, attempt, "PERMANENT", "岗位快照缺失")
        raise PermanentTaskError("岗位快照缺失")

    blocks = list(
        db.scalars(
            select(ResumeBlock)
            .where(ResumeBlock.resume_version_id == run.resume_version_id)
            .order_by(ResumeBlock.ordinal)
        ).all()
    )
    if not blocks:
        _fail(db, tasks, task, attempt, "PERMANENT", "简历版本没有可分析的原文块")
        raise PermanentTaskError("简历版本没有可分析的原文块")

    snapshot = AnalysisSnapshot(
        job_version_id=run.job_version_id,
        rubric_version_id=run.rubric_version_id,
        resume_version_id=run.resume_version_id,
        job_title=snapshot_data["job"].title,
        job_description=snapshot_data["job_version"].description,
        requirements=snapshot_data["requirements"],
        resume_blocks=[{"id": block.id, "section": block.section, "text": block.text} for block in blocks],
    )
    valid_block_ids = {block.id for block in blocks}

    state = run_matching(
        snapshot=snapshot,
        rubric=snapshot_data["rubric"],
        valid_block_ids=valid_block_ids,
        gateway=ModelGateway(),
    )

    if state.needs_review:
        run.status = "NEEDS_REVIEW"
        run.error_code = state.review_reason
        candidate = db.get(Candidate, run.candidate_id)
        if candidate is not None:
            candidate.status = "NEEDS_REVIEW"
        tasks.mark_review(
            task,
            attempt=attempt,
            error_code=state.review_reason or "REVIEW_REQUIRED",
            error_summary="；".join(state.agent_errors) or "分析结果需人工复核",
        )
        _persist_agent_runs(db, run, state)
        db.commit()
        return

    assert state.score is not None, "成功路径必须产生分数"
    run.status = "SUCCEEDED"
    run.total_score = state.score.score
    run.confidence = state.score.confidence
    run.route = state.score.route
    run.prompt_version = PROMPT_VERSION
    candidate = db.get(Candidate, run.candidate_id)
    if candidate is not None:
        previous = candidate.status
        candidate.status = state.score.route
        db.add(
            RoutingDecision(
                candidate_id=candidate.id,
                analysis_run_id=run.id,
                source="MATCHING",
                from_status=previous,
                to_status=state.score.route,
                reason=f"score={state.score.score:.1f} confidence={state.score.confidence:.2f}",
            )
        )
    _persist_agent_runs(db, run, state)
    tasks.update_progress(task, progress=100)
    tasks.finish(task, attempt=attempt, succeeded=True)
    db.commit()


def _persist_agent_runs(db: Session, run: AnalysisRun, state) -> None:
    for dimension in DIMENSIONS:
        result = state.agent_results.get(dimension)
        if result is None:
            continue
        db.add(
            AgentRun(
                analysis_run_id=run.id,
                agent_type=dimension,
                status="SUCCEEDED",
                score=result.raw_score,
                confidence=result.confidence,
                evidence_json=json.dumps(result.evidence_refs, ensure_ascii=False),
                reason_codes_json=json.dumps(result.reason_codes, ensure_ascii=False),
            )
        )
    db.add(
        ModelRun(
            org_id=run.org_id,
            purpose="matching",
            provider="openai-compatible",
            model="chat",
            prompt_version=PROMPT_VERSION,
            status="SUCCEEDED" if not state.needs_review else "FAILED",
            latency_ms=0,
            error_code=state.review_reason if state.needs_review else None,
        )
    )


def _fail(db: Session, tasks: TaskService, task: Task, attempt, error_code: str, summary: str) -> None:
    tasks.schedule_retry(task, attempt=attempt, error_code=error_code, error_summary=summary)
    db.commit()
