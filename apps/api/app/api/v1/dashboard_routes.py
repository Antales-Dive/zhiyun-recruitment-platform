"""仪表盘路由：授权事实统计与轻量指标（FR-034）。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.auth import require_any_role
from app.api.envelope import success
from app.domains.analytics.service import dashboard_facts, metrics_text
from app.domains.identity.principal import Principal
from app.infrastructure.db import get_db

router = APIRouter(prefix="/api/v1")


@router.get("/dashboard")
def dashboard_route(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR")),
):
    return success(request, dashboard_facts(db, org_id=principal.org_id))


@router.get("/metrics", response_class=PlainTextResponse)
def metrics_route(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "AUDITOR")),
):
    """轻量指标文本：授权范围内事实计算；不部署完整观测套件（NFR-007）。"""
    return PlainTextResponse(metrics_text(db, org_id=principal.org_id))
