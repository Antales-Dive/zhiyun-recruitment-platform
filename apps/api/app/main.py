from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.envelope import error_payload
from app.api.middleware.context import RequestContextMiddleware
from app.api.v1 import (
    analysis_routes,
    assistant_routes,
    audit_routes,
    auth_routes,
    candidate_routes,
    dashboard_routes,
    interview_routes,
    job_routes,
    knowledge_routes,
    questionnaire_routes,
    schedule_routes,
    task_routes,
)
from app.api.v1.routes import router
from app.config import settings
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import get_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 禁止运行时自动建表/改表：schema 由 Alembic 迁移管理（NFR-008）
    yield


app = FastAPI(title="智聘云智能招聘平台", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RequestContextMiddleware)
app.include_router(router)
app.include_router(knowledge_routes.router)
app.include_router(auth_routes.router)
app.include_router(audit_routes.router)
app.include_router(task_routes.router)
app.include_router(candidate_routes.router)
app.include_router(job_routes.router)
app.include_router(analysis_routes.router)
app.include_router(assistant_routes.router)
app.include_router(questionnaire_routes.router)
app.include_router(questionnaire_routes.public_router)
app.include_router(schedule_routes.router)
app.include_router(schedule_routes.public_router)
app.include_router(interview_routes.router)
app.include_router(interview_routes.public_router)
app.include_router(dashboard_routes.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = exc.detail if isinstance(exc.detail, str) else "HTTP_ERROR"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code=code, message=code, retryable=exc.status_code >= 500),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in error["loc"]), "reason": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_payload(
            request,
            code="VALIDATION_ERROR",
            message="请求参数不符合要求",
            details=details,
        ),
    )


@app.get("/health")
@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "service": "zhiyun-api"}


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        # 连接探活：常量查询，不涉及任何用户输入
        db.scalar(select(1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="DATABASE_UNAVAILABLE") from exc
    return {"status": "ready", "service": "zhiyun-api", "environment": settings.app_env}
