"""任务路由：快照、尝试记录与 Last-Event-ID 补发的 SSE。"""
import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_any_role
from app.api.envelope import success
from app.config import settings
from app.domains.identity.principal import Principal
from app.domains.tasks import stream
from app.domains.tasks.service import TaskService
from app.infrastructure.db import get_db
from app.infrastructure.models import Task

router = APIRouter(prefix="/api/v1")


def _get_org_task(db: Session, task_id: str, org_id: str) -> Task | None:
    return db.scalar(select(Task).where(Task.id == task_id, Task.org_id == org_id))


@router.get("/tasks/{task_id}")
def get_task_snapshot(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR", "INTERVIEWER")),
):
    task = _get_org_task(db, task_id, principal.org_id)
    if not task:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")
    attempts = TaskService(db).attempts(task_id=task.id)
    return success(
        request,
        {
            "task_id": task.id,
            "candidate_id": task.candidate_id,
            "aggregate_type": task.aggregate_type,
            "aggregate_id": task.aggregate_id,
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "current_attempt": task.current_attempt,
            "next_retry_at": task.next_retry_at.isoformat() if task.next_retry_at else None,
            "last_error_code": task.last_error_code,
            "attempts": [
                {
                    "attempt_no": attempt.attempt_no,
                    "status": attempt.status,
                    "started_at": attempt.started_at.isoformat(),
                    "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
                    "error_code": attempt.error_code,
                    "error_summary": attempt.error_summary,
                }
                for attempt in attempts
            ],
        },
    )


def _sse_event(event) -> str:
    data = json.dumps(json.loads(event.data_json), ensure_ascii=False)
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_role("ADMIN", "HR", "INTERVIEWER")),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    task = _get_org_task(db, task_id, principal.org_id)
    if not task:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")

    after_sequence: int | None = None
    if last_event_id:
        try:
            after_sequence = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="INVALID_EVENT_ID") from None
        oldest = stream.oldest_sequence(db, org_id=principal.org_id, stream_id=task.id)
        if oldest is not None and after_sequence < oldest:
            raise HTTPException(status_code=409, detail="EVENT_CURSOR_EXPIRED")

    events = stream.replay(db, org_id=principal.org_id, stream_id=task.id, after_sequence=after_sequence)

    async def event_generator():
        for event in events:
            yield _sse_event(event)
        while True:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(settings.sse_heartbeat_seconds)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
