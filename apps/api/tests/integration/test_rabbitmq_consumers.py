"""RabbitMQ Consumer 集成测试：幂等、重试、DLQ 与 schema 拒绝。

前置：docker compose 已启动 mysql/redis/rabbitmq。
"""
import json
import threading
import time

import pika
import pytest
from app.config import settings
from app.domains.tasks.errors import PermanentTaskError, RetryableTaskError
from app.infrastructure.db import SessionLocal
from app.infrastructure.messaging.connection import connect, declare_topology
from app.infrastructure.messaging.consumer import ConsumerRuntime
from app.infrastructure.messaging.envelope import build_envelope
from app.infrastructure.models import ConsumerDelivery

WAIT_TIMEOUT_SECONDS = 15
SIDE_EFFECTS: list[str] = []


def _reset_side_effects():
    SIDE_EFFECTS.clear()


@pytest.fixture(scope="module", autouse=True)
def schema():
    from app.infrastructure import models  # noqa: F401
    from app.infrastructure.db import Base, engine

    Base.metadata.create_all(bind=engine)
    yield


def make_handler(behavior: dict):
    """behavior: {"fail_first": n, "error": ExceptionType}"""

    def handler(db, envelope):
        key = envelope.event_id
        if key in behavior:
            remaining = behavior[key]
            if remaining > 0:
                behavior[key] = remaining - 1
                error_type = behavior.get("error", RetryableTaskError)
                raise error_type(f"模拟失败，剩余 {remaining} 次")
        SIDE_EFFECTS.append(key)

    return handler


@pytest.fixture(scope="module")
def channel():
    connection = connect()
    channel = connection.channel()
    declare_topology(channel)
    channel.queue_purge(settings.rabbitmq_main_queue)
    channel.queue_purge(settings.rabbitmq_retry_queue)
    channel.queue_purge(settings.rabbitmq_dlq)
    yield channel
    channel.close()
    connection.close()


def _publish(channel, envelope: dict) -> None:
    channel.basic_publish(
        exchange=settings.rabbitmq_exchange,
        routing_key="#",
        body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
    )


def _envelope(event_id: str, event_type: str = "test.handler", schema_version: int = 1) -> dict:
    return build_envelope(
        event_id=event_id,
        event_type=event_type,
        aggregate_type="candidate",
        aggregate_id="cand-1",
        org_id="org-a",
        trace_id="test",
        payload={},
        schema_version=schema_version,
    )


def _wait_until(predicate, timeout=WAIT_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _ledger_row(event_id: str) -> ConsumerDelivery | None:
    from sqlalchemy import select

    db = SessionLocal()
    try:
        return db.scalar(
            select(ConsumerDelivery).where(
                ConsumerDelivery.consumer_name == "zhiyun-worker",
                ConsumerDelivery.event_id == event_id,
            )
        )
    finally:
        db.close()


def _queue_message_count(channel, queue: str) -> int:
    method = channel.queue_declare(queue=queue, passive=True)
    return method.method.message_count


class TestConsumerHappyPath:
    def test_handles_message_once_and_records_ledger(self, channel):
        _reset_side_effects()
        event_id = "happy-1"
        _publish(channel, _envelope(event_id))

        runtime = ConsumerRuntime("zhiyun-worker", {"test.handler": make_handler({})})
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: event_id in SIDE_EFFECTS)
            assert _wait_until(lambda: _ledger_row(event_id) is not None)
        finally:
            runtime.stop()
            thread.join(timeout=5)

        assert _ledger_row(event_id).status == "SUCCEEDED"

    def test_duplicate_delivery_has_single_side_effect(self, channel):
        _reset_side_effects()
        event_id = "dup-1"
        _publish(channel, _envelope(event_id))
        _publish(channel, _envelope(event_id))

        runtime = ConsumerRuntime("zhiyun-worker", {"test.handler": make_handler({})})
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: _ledger_row(event_id) is not None)
            # 第二条重复消息到达后不产生第二次副作用
            time.sleep(1.5)
        finally:
            runtime.stop()
            thread.join(timeout=5)

        assert SIDE_EFFECTS.count(event_id) == 1


class TestConsumerRetry:
    def test_retryable_failure_then_success(self, channel):
        _reset_side_effects()
        event_id = "retry-1"
        _publish(channel, _envelope(event_id))

        behavior = {event_id: 2, "error": RetryableTaskError}
        runtime = ConsumerRuntime("zhiyun-worker", {"test.handler": make_handler(behavior)})
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            # 前两次失败走重试队列（TTL 后回主队列），第三次成功
            assert _wait_until(
                lambda: _ledger_row(event_id) is not None
                and _ledger_row(event_id).status == "SUCCEEDED",
                timeout=WAIT_TIMEOUT_SECONDS,
            )
        finally:
            runtime.stop()
            thread.join(timeout=5)

        row = _ledger_row(event_id)
        assert row.status == "SUCCEEDED"
        assert row.attempt_no >= 3
        assert SIDE_EFFECTS.count(event_id) == 1

    def test_permanent_failure_goes_to_dlq(self, channel):
        event_id = "permanent-1"
        _publish(channel, _envelope(event_id))

        runtime = ConsumerRuntime(
            "zhiyun-worker",
            {"test.handler": make_handler({event_id: 99, "error": PermanentTaskError})},
        )
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: _ledger_row(event_id) is not None)
        finally:
            runtime.stop()
            thread.join(timeout=5)

        row = _ledger_row(event_id)
        assert row.status == "DEAD"
        assert _queue_message_count(channel, settings.rabbitmq_dlq) >= 1

    def test_max_attempts_exhausted_goes_to_dlq(self, channel):
        event_id = "exhaust-1"
        _publish(channel, _envelope(event_id))

        runtime = ConsumerRuntime(
            "zhiyun-worker",
            {"test.handler": make_handler({event_id: 999, "error": RetryableTaskError})},
        )
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(
                lambda: _ledger_row(event_id) is not None and _ledger_row(event_id).status == "DEAD",
                timeout=WAIT_TIMEOUT_SECONDS,
            )
        finally:
            runtime.stop()
            thread.join(timeout=5)

        assert _ledger_row(event_id).status == "DEAD"
        assert _queue_message_count(channel, settings.rabbitmq_dlq) >= 1


class TestConsumerSchemaGuard:
    def test_unsupported_schema_goes_to_dlq(self, channel):
        event_id = "schema-1"
        _publish(channel, _envelope(event_id, schema_version=2))

        runtime = ConsumerRuntime("zhiyun-worker", {})
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: _queue_message_count(channel, settings.rabbitmq_dlq) >= 1)
        finally:
            runtime.stop()
            thread.join(timeout=5)

        # 未知 schema 不进入账本（在解析阶段即拒绝）
        assert _ledger_row(event_id) is None

    def test_unregistered_event_type_goes_to_dlq(self, channel):
        event_id = "unregistered-1"
        _publish(channel, _envelope(event_id, event_type="no.such.handler"))

        runtime = ConsumerRuntime("zhiyun-worker", {})
        thread = threading.Thread(target=runtime.start, daemon=True)
        thread.start()
        try:
            assert _wait_until(lambda: _ledger_row(event_id) is not None)
        finally:
            runtime.stop()
            thread.join(timeout=5)

        assert _ledger_row(event_id).status == "DEAD"
