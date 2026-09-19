"""统计域：从事实库计算授权统计与轻量指标（FR-034）。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.models import (
    AnalysisRun,
    AssistantQueryLog,
    Candidate,
    ModelRun,
    OutboxEvent,
    Task,
)


def dashboard_facts(db: Session, *, org_id: str) -> dict:
    """仪表盘事实：全部来自事实库可计算数据，不维护手工计数。"""
    candidates = db.scalar(
        select(func.count(Candidate.id)).where(Candidate.org_id == org_id)
    ) or 0
    parsed = db.scalar(
        select(func.count(Candidate.id)).where(Candidate.org_id == org_id, Candidate.status == "PARSED")
    ) or 0
    pending_tasks = db.scalar(
        select(func.count(Task.id)).where(
            Task.org_id == org_id, Task.status.in_(["PENDING", "PROCESSING", "RETRY_WAIT"])
        )
    ) or 0
    failed_tasks = db.scalar(
        select(func.count(Task.id)).where(Task.org_id == org_id, Task.status == "FAILED")
    ) or 0
    needs_review = db.scalar(
        select(func.count(Candidate.id)).where(Candidate.org_id == org_id, Candidate.status == "NEEDS_REVIEW")
    ) or 0

    routed = db.scalars(
        select(AnalysisRun.route, func.count(AnalysisRun.id))
        .where(AnalysisRun.org_id == org_id, AnalysisRun.status == "SUCCEEDED")
        .group_by(AnalysisRun.route)
    ).all()
    route_counts = {route: count for route, count in routed}

    # RAG 拒答率：授权范围内查询中无可靠证据的比例
    query_total = db.scalar(
        select(func.count(AssistantQueryLog.id)).where(AssistantQueryLog.org_id == org_id)
    ) or 0
    refusal = db.scalar(
        select(func.count(AssistantQueryLog.id)).where(
            AssistantQueryLog.org_id == org_id,
            AssistantQueryLog.error_code == "NO_RELIABLE_EVIDENCE",
        )
    ) or 0

    return {
        "total": candidates,
        "parsed": parsed,
        "pending": pending_tasks,
        "failed": failed_tasks,
        "needs_review": needs_review,
        "routes": route_counts,
        "rag_queries": query_total,
        "rag_refusals": refusal,
        "rag_refusal_rate": round(refusal / query_total, 3) if query_total else 0.0,
    }


def metrics_text(db: Session, *, org_id: str) -> str:
    """轻量 Prometheus 文本指标；队列深度来自 outbox（Redis 不可用时仍可观测）。"""
    facts = dashboard_facts(db, org_id=org_id)
    queue_depth = db.scalar(
        select(func.count(OutboxEvent.id)).where(
            OutboxEvent.org_id == org_id,
            OutboxEvent.status == "PENDING",
        )
    ) or 0
    model_errors = db.scalar(
        select(func.count(ModelRun.id)).where(ModelRun.org_id == org_id, ModelRun.status == "FAILED")
    ) or 0
    lines = [
        "# TYPE zhiyun_candidates gauge",
        f"zhiyun_candidates_total {facts['total']}",
        f"zhiyun_candidates_parsed {facts['parsed']}",
        f"zhiyun_candidates_needs_review {facts['needs_review']}",
        "# TYPE zhiyun_tasks gauge",
        f"zhiyun_tasks_pending {facts['pending']}",
        f"zhiyun_tasks_failed {facts['failed']}",
        "# TYPE zhiyun_outbox_pending gauge",
        f"zhiyun_outbox_pending {queue_depth}",
        "# TYPE zhiyun_model_runs_failed gauge",
        f"zhiyun_model_runs_failed {model_errors}",
        "# TYPE zhiyun_rag_refusal_rate gauge",
        f"zhiyun_rag_refusal_rate {facts['rag_refusal_rate']}",
    ]
    return "\n".join(lines) + "\n"
