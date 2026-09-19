"""流事件与幂等记录单元测试。"""
import pytest
from app.domains.tasks import stream
from app.domains.tasks.idempotency import IdempotencyConflict, begin, complete, request_digest
from app.infrastructure.models import IdempotencyRecord
from sqlalchemy import select


class TestStreamStore:
    def test_append_and_replay_in_order(self, db):
        stream.append(
            db,
            org_id="org-a",
            stream_type="task",
            stream_id="t1",
            event_type="task.created",
            data={"status": "PENDING"},
        )
        stream.append(
            db,
            org_id="org-a",
            stream_type="task",
            stream_id="t1",
            event_type="task.progress",
            data={"progress": 50},
        )
        db.commit()

        events = stream.replay(db, org_id="org-a", stream_id="t1")

        assert [event.event_type for event in events] == ["task.created", "task.progress"]
        assert events[0].sequence < events[1].sequence

    def test_replay_after_sequence_skips_earlier_events(self, db):
        stream.append(
            db, org_id="org-a", stream_type="task", stream_id="t1", event_type="task.created", data={}
        )
        second = stream.append(
            db, org_id="org-a", stream_type="task", stream_id="t1", event_type="task.progress", data={"progress": 50}
        )
        db.commit()

        events = stream.replay(db, org_id="org-a", stream_id="t1", after_sequence=second.sequence)

        assert events == []

    def test_stream_is_org_and_id_scoped(self, db):
        stream.append(
            db, org_id="org-a", stream_type="task", stream_id="t1", event_type="task.created", data={}
        )
        stream.append(
            db, org_id="org-b", stream_type="task", stream_id="t1", event_type="task.created", data={}
        )
        db.commit()

        assert (
            len(stream.replay(db, org_id="org-a", stream_id="t1")) == 1
        )
        assert stream.oldest_sequence(db, org_id="org-a", stream_id="t1") is not None
        assert stream.latest_sequence(db, org_id="org-a", stream_id="t1") is not None

    def test_oldest_sequence_is_none_for_empty_stream(self, db):
        assert stream.oldest_sequence(db, org_id="org-a", stream_id="missing") is None


class TestIdempotency:
    def test_begin_then_complete_then_reuse(self, db):
        digest = request_digest("payload-1")
        assert (
            begin(db, org_id="org-a", actor_id="u1", operation="candidate.import", key="k1", request_hash=digest)
            is None
        )

        record = db.scalar(select(IdempotencyRecord))
        complete(db, record, response_status=202, response_json={"task_id": "t1"})
        db.commit()

        reused = begin(db, org_id="org-a", actor_id="u1", operation="candidate.import", key="k1", request_hash=digest)
        assert reused is not None
        assert reused.response_status == 202

    def test_same_key_different_payload_conflicts(self, db):
        begin(
            db, org_id="org-a", actor_id="u1", operation="op", key="k1", request_hash=request_digest("a")
        )
        db.commit()

        with pytest.raises(IdempotencyConflict):
            begin(db, org_id="org-a", actor_id="u1", operation="op", key="k1", request_hash=request_digest("b"))

    def test_same_key_different_scope_is_independent(self, db):
        begin(
            db, org_id="org-a", actor_id="u1", operation="op", key="k1", request_hash=request_digest("a")
        )
        db.commit()

        assert (
            begin(db, org_id="org-b", actor_id="u1", operation="op", key="k1", request_hash=request_digest("a"))
            is None
        )
