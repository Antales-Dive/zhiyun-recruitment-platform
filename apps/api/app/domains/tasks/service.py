"""任务服务：任务快照、尝试记录、进度事件与状态迁移。"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domains.tasks import stream
from app.infrastructure.models import Task, TaskAttempt, now_utc

VALID_STATUSES = {"PENDING", "PROCESSING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "NEEDS_REVIEW", "CANCELLED"}


class TaskNotFoundError(LookupError):
    pass


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        org_id: str,
        task_type: str,
        aggregate_type: str,
        aggregate_id: str,
    ) -> Task:
        task = Task(
            org_id=org_id,
            task_type=task_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            status="PENDING",
            progress=0,
            current_attempt=0,
        )
        self.db.add(task)
        self.db.flush()
        stream.append(
            self.db,
            org_id=org_id,
            stream_type="task",
            stream_id=task.id,
            event_type="task.created",
            data={"task_id": task.id, "status": task.status},
        )
        return task

    def snapshot(self, *, task_id: str, org_id: str) -> Task | None:
        return self.db.scalar(
            select(Task).where(Task.id == task_id, Task.org_id == org_id)
        )

    def start_attempt(self, task: Task) -> TaskAttempt:
        attempt_no = task.current_attempt + 1
        task.current_attempt = attempt_no
        task.status = "PROCESSING"
        task.next_retry_at = None
        attempt = TaskAttempt(task_id=task.id, attempt_no=attempt_no, status="PROCESSING")
        self.db.add(attempt)
        self.db.flush()
        return attempt

    def update_progress(self, task: Task, *, progress: int, status: str | None = None) -> None:
        task.progress = progress
        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"非法任务状态：{status}")
            task.status = status
        stream.append(
            self.db,
            org_id=task.org_id,
            stream_type="task",
            stream_id=task.id,
            event_type="task.progress",
            data={"task_id": task.id, "status": task.status, "progress": task.progress},
        )

    def finish(self, task: Task, *, attempt: TaskAttempt, succeeded: bool) -> None:
        attempt.finished_at = now_utc()
        attempt.status = "SUCCEEDED" if succeeded else "FAILED"
        task.status = "SUCCEEDED" if succeeded else "FAILED"
        task.progress = 100 if succeeded else task.progress
        stream.append(
            self.db,
            org_id=task.org_id,
            stream_type="task",
            stream_id=task.id,
            event_type="task.completed" if succeeded else "task.failed",
            data={"task_id": task.id, "status": task.status, "progress": task.progress},
        )

    def fail_permanently(self, task: Task, *, attempt: TaskAttempt, error_code: str, error_summary: str) -> None:
        attempt.finished_at = now_utc()
        attempt.status = "FAILED"
        attempt.error_code = error_code
        attempt.error_summary = (error_summary or "")[:500]
        task.status = "FAILED"
        task.next_retry_at = None
        task.last_error = (error_summary or "")[:2000]
        task.last_error_code = error_code
        task.last_error = (error_summary or "")[:2000]
        stream.append(
            self.db,
            org_id=task.org_id,
            stream_type="task",
            stream_id=task.id,
            event_type="task.failed",
            data={"task_id": task.id, "status": task.status, "progress": task.progress},
        )

    def schedule_retry(self, task: Task, *, attempt: TaskAttempt, error_code: str, error_summary: str) -> None:
        attempt.finished_at = now_utc()
        attempt.status = "RETRY_WAIT"
        attempt.error_code = error_code
        attempt.error_summary = (error_summary or "")[:500]
        task.status = "RETRY_WAIT"
        task.next_retry_at = now_utc() + timedelta(seconds=settings.message_retry_delay_ms / 1000)
        task.last_error_code = error_code

    def mark_review(self, task: Task, *, attempt: TaskAttempt, error_code: str, error_summary: str) -> None:
        attempt.finished_at = now_utc()
        attempt.status = "NEEDS_REVIEW"
        attempt.error_code = error_code
        attempt.error_summary = (error_summary or "")[:500]
        task.status = "NEEDS_REVIEW"
        task.last_error_code = error_code

    def attempts(self, *, task_id: str) -> list[TaskAttempt]:
        return list(
            self.db.scalars(
                select(TaskAttempt).where(TaskAttempt.task_id == task_id).order_by(TaskAttempt.attempt_no)
            ).all()
        )
