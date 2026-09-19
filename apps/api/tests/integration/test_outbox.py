"""Outbox 集成测试：事务边界与真实 RabbitMQ 发布。

前置：docker compose 已启动 mysql/redis/rabbitmq。
"""
import json

import pytest
from app.config import settings
from app.domains.tasks.outbox import OutboxService
from app.infrastructure.db import SessionLocal
from app.infrastructure.messaging.connection import connect, declare_topology
from app.infrastructure.messaging.envelope import build_envelope
from app.infrastructure.messaging.publisher import OutboxPublisher
from app.infrastructure.models import OutboxEvent

EVENT_PAYLOAD = {"task_id": "task-1", "file_id": "file-1"}


@pytest.fixture(scope="module", autouse=True)
def schema():
    from app.infrastructure import models  # noqa: F401
    from app.infrastructure.db import Base, engine

    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def clean_state():
    """清理测试间的 outbox/账本残留，保证取批断言隔离。"""
    from app.infrastructure.models import ConsumerDelivery, OutboxEvent

    db = SessionLocal()
    try:
        db.query(OutboxEvent).delete()
        db.query(ConsumerDelivery).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(scope="module")
def channel():
    connection = connect()
    channel = connection.channel()
    declare_topology(channel)
    # 清空队列，保证测试隔离
    channel.queue_purge(settings.rabbitmq_main_queue)
    channel.queue_purge(settings.rabbitmq_retry_queue)
    channel.queue_purge(settings.rabbitmq_dlq)
    yield channel
    channel.close()
    connection.close()


def _enqueue(db, *, org_id="org-a", event_type="resume.parse.requested"):
    return OutboxService(db).enqueue(
        org_id=org_id,
        event_type=event_type,
        aggregate_type="candidate",
        aggregate_id="cand-1",
        payload=EVENT_PAYLOAD,
    )


class TestOutboxTransaction:
    def test_rollback_discards_business_write_and_event_together(self):
        db = SessionLocal()
        event = _enqueue(db)
        db.rollback()
        db.close()

        db = SessionLocal()
        assert db.get(OutboxEvent, event.id) is None
        db.close()

    def test_commit_persists_event(self):
        db = SessionLocal()
        event = _enqueue(db)
        db.commit()
        db.close()

        db = SessionLocal()
        assert db.get(OutboxEvent, event.id) is not None
        db.close()


class TestOutboxPublish:
    def test_publish_delivers_message_and_marks_published(self, channel):
        db = SessionLocal()
        event = _enqueue(db, event_type="test.publish.flow")
        db.commit()
        db.close()

        db = SessionLocal()
        outbox = OutboxService(db)
        claimed = outbox.claim_batch(batch_size=10, lease_seconds=30)
        assert [item.id for item in claimed] == [event.id]
        envelope = build_envelope(
            event_id=event.id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            org_id=event.org_id,
            trace_id="test",
            payload=json.loads(event.payload_json),
        )
        publisher = OutboxPublisher()
        confirmed = publisher.publish(envelope)
        publisher.close()
        assert confirmed is True

        outbox.mark_published(event.id)
        db.close()

        db = SessionLocal()
        published = db.get(OutboxEvent, event.id)
        assert published.status == "PUBLISHED"
        assert published.published_at is not None
        db.close()

        # 消息已进入主队列
        method, properties, body = channel.basic_get(settings.rabbitmq_main_queue, auto_ack=True)
        assert method is not None
        delivered = json.loads(body)
        assert delivered["event_id"] == event.id
        assert delivered["payload"] == EVENT_PAYLOAD

    def test_duplicate_publish_is_handled_by_consumer_ledger(self, channel):
        """confirm 丢失导致重复投递：Consumer Ledger 保证只产生一次业务效果。

        完整幂等验证在 test_rabbitmq_consumers.py；这里验证重复发布后
        主队列出现两条相同 event_id 的消息。
        """
        db = SessionLocal()
        event = _enqueue(db, event_type="test.duplicate.flow")
        db.commit()
        db.close()

        publisher = OutboxPublisher()
        envelope = build_envelope(
            event_id=event.id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            org_id=event.org_id,
            trace_id="test",
            payload=EVENT_PAYLOAD,
        )
        assert publisher.publish(envelope) is True
        assert publisher.publish(envelope) is True
        publisher.close()

        first = channel.basic_get(settings.rabbitmq_main_queue, auto_ack=True)
        second = channel.basic_get(settings.rabbitmq_main_queue, auto_ack=True)
        assert first[0] is not None and second[0] is not None
        assert json.loads(first[2])["event_id"] == json.loads(second[2])["event_id"] == event.id
