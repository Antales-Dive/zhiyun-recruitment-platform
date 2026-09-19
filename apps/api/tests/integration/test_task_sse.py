"""任务 SSE 集成测试：Last-Event-ID 补发与游标过期（真实 uvicorn 流式）。"""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from app.domains.tasks import stream
from app.domains.tasks.service import TaskService
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine

SSE_HEADERS = {"X-User-Role": "HR", "X-Org-Id": "default"}


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(scope="module")
def server_url():
    from app.main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def create_task_with_events(org_id="default"):
    db = SessionLocal()
    try:
        service = TaskService(db)
        task = service.create(
            org_id=org_id,
            task_type="TEST_FLOW",
            aggregate_type="candidate",
            aggregate_id="cand-x",
        )
        attempt = service.start_attempt(task)
        service.update_progress(task, progress=30, status="PROCESSING")
        service.finish(task, attempt=attempt, succeeded=True)
        db.commit()
        return task.id
    finally:
        db.close()


def read_sse(base_url: str, task_id: str, last_event_id: str | None = None) -> tuple[int, str]:
    headers = dict(SSE_HEADERS)
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)

    with httpx.Client(timeout=10) as client:
        with client.stream("GET", f"{base_url}/api/v1/tasks/{task_id}/events", headers=headers) as response:
            chunks = []
            for chunk in response.iter_text():
                chunks.append(chunk)
                if ": keep-alive" in "".join(chunks):
                    break
            return response.status_code, "".join(chunks)


class TestTaskSse:
    def test_full_stream_contains_persisted_events(self, server_url):
        task_id = create_task_with_events()

        status, body = read_sse(server_url, task_id)

        assert status == 200
        assert "event: task.created" in body
        assert "event: task.progress" in body
        assert "event: task.completed" in body
        assert ": keep-alive" in body

    def test_replay_after_last_event_id_only_sends_later_events(self, server_url):
        task_id = create_task_with_events()
        db = SessionLocal()
        try:
            events = stream.replay(db, org_id="default", stream_id=task_id)
            first_sequence = events[0].sequence
            second_sequence = events[1].sequence
        finally:
            db.close()

        status, body = read_sse(server_url, task_id, last_event_id=first_sequence)

        assert status == 200
        assert f"id: {second_sequence}" in body
        assert "task.created" not in body
        assert "task.progress" in body

    def test_cursor_older_than_retention_returns_conflict(self, server_url):
        task_id = create_task_with_events()

        status, body = read_sse(server_url, task_id, last_event_id=0)

        assert status == 409
        assert "EVENT_CURSOR_EXPIRED" in body

    def test_cross_org_task_is_not_accessible(self, server_url):
        task_id = create_task_with_events(org_id="other-org")

        response = httpx.get(
            f"{server_url}/api/v1/tasks/{task_id}",
            headers=SSE_HEADERS,
            timeout=10,
        )

        assert response.status_code == 404
