"""AI 面试 E2E：SSE 断线重连只补发后续事件，不重复 AI 发言（AC-009）。

使用真实 uvicorn 服务器验证流式行为（httpx ASGITransport 不流式）。
"""
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from app.domains.interviews.service import (
    append_candidate_message,
    create_invitation,
    create_session,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, Job, JobVersion, ResumeVersion, RubricVersion


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


def prepare_session(db):
    """创建岗位/候选人/简历版本与面试会话，返回 (session_id, token)。"""
    org = f"org-{uuid.uuid4().hex[:6]}"
    job = Job(org_id=org, title="后端工程师", description="d", skills_json="[]")
    db.add(job)
    db.flush()
    candidate = Candidate(org_id=org, job_id=job.id, name="候选人")
    db.add(candidate)
    db.flush()
    resume = ResumeVersion(
        candidate_id=candidate.id, file_id="", version_no=1, status="PARSED",
        parser_version="resume-parser-1", quality_json="{}",
    )
    db.add(resume)
    db.flush()
    job_version = JobVersion(job_id=job.id, version_no=1, description="d", requirements_json="[]")
    db.add(job_version)
    db.flush()
    db.add(RubricVersion(job_version_id=job_version.id, version_no=1, dimensions_json="{}", thresholds_json="{}"))
    job.active_version_id = job_version.id
    db.commit()

    session = create_session(
        db,
        org_id=org,
        candidate_id=candidate.id,
        job_version_id=job_version.id,
        resume_version_id=resume.id,
        plan={"questions": ["请介绍后端经验", "如何处理线上事故？"], "max_rounds": 3, "forbidden": []},
    )
    _, token = create_invitation(db, session_id=session.id, org_id=org)
    return session.id, token, org


def read_sse_events(base_url: str, token: str, last_event_id: int | None = None, timeout: float = 10.0) -> list[dict]:
    """读取 SSE 直到心跳出现，返回事件列表（含服务端持久化序号 id）。"""
    headers = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    events: list[dict] = []
    current_id: int | None = None
    current_event = "message"
    with httpx.Client(timeout=timeout) as client:
        with client.stream("GET", f"{base_url}/public/interviews/{token}/events", headers=headers) as response:
            assert response.status_code == 200
            for chunk in response.iter_text():
                if ": keep-alive" in chunk:
                    break
                for line in chunk.split("\n"):
                    if line.startswith("id: "):
                        current_id = int(line[4:].strip())
                    elif line.startswith("event: "):
                        current_event = line[7:].strip()
                    elif line.startswith("data: ") and current_id is not None:
                        events.append({"id": current_id, "event": current_event})
    return events


class TestInterviewReconnect:
    def test_reconnect_with_last_event_id_only_replays_new_events(self, server_url):
        db = SessionLocal()
        try:
            session_id, token, _ = prepare_session(db)

            # 第一轮：候选人消息（Provider 未配置 → AI 回复为显式错误，不产生 AI 消息）
            append_candidate_message(
                db, token=token, client_message_id=f"msg-{uuid4().hex[:8]}", content="你好，我准备好了"
            )
            db.commit()

            first_events = read_sse_events(server_url, token)
            assert len(first_events) >= 1
            assert first_events[0]["event"] in {"interview.message", "interview.state_changed"}

            # 第二轮：新候选人消息产生新事件
            append_candidate_message(
                db, token=token, client_message_id=f"msg-{uuid4().hex[:8]}", content="我主要负责 API 服务"
            )
            db.commit()

            # 携带 Last-Event-ID 重连：只收到第一轮之后的事件（无重复）
            last_seen = max(event["id"] for event in first_events)
            reconnect_events = read_sse_events(server_url, token, last_event_id=last_seen)
            assert len(reconnect_events) >= 1
            assert all(event["id"] > last_seen for event in reconnect_events)
        finally:
            db.close()

    def test_ai_never_repeats_after_reconnect(self, server_url):
        """断线重连不会触发 AI 重复发言：重连只读事件流，不写消息。"""
        db = SessionLocal()
        try:
            session_id, token, _ = prepare_session(db)
            append_candidate_message(
                db, token=token, client_message_id=f"msg-{uuid4().hex[:8]}", content="测试重连不重发"
            )
            db.commit()

            from app.infrastructure.models import InterviewMessage

            before = db.query(InterviewMessage).filter_by(session_id=session_id).count()

            events = read_sse_events(server_url, token)
            last_seen = max(event["id"] for event in events)
            read_sse_events(server_url, token, last_event_id=last_seen)

            after = db.query(InterviewMessage).filter_by(session_id=session_id).count()
            assert after == before  # SSE 读取不产生任何新消息
        finally:
            db.close()


def uuid4():
    return uuid.uuid4()
