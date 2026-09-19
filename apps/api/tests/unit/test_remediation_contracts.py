import json

import pytest
from app.domains.tasks.errors import PermanentTaskError
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
from app.infrastructure.models import Task
from app.workers.knowledge_worker import handle_knowledge_ingestion_requested
from app.workers.main import HANDLERS


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


def test_notification_event_has_registered_handler():
    assert "notification.requested" in HANDLERS


def test_permanent_knowledge_error_finishes_task_as_failed():
    db = SessionLocal()
    try:
        task = Task(
            org_id="org-a",
            task_type="KNOWLEDGE_INGEST",
            aggregate_type="knowledge_version",
            aggregate_id="missing-version",
            status="PENDING",
        )
        db.add(task)
        db.commit()
        envelope = parse_envelope(
            json.dumps(
                build_envelope(
                    event_id="evt-permanent-task",
                    event_type="knowledge.ingestion.requested",
                    aggregate_type="knowledge_version",
                    aggregate_id="missing-version",
                    org_id="org-a",
                    trace_id="test",
                    payload={"task_id": task.id, "version_id": "missing-version"},
                )
            )
        )

        with pytest.raises(PermanentTaskError):
            handle_knowledge_ingestion_requested(db, envelope)

        db.expire_all()
        assert db.get(Task, task.id).status == "FAILED"
    finally:
        db.close()
