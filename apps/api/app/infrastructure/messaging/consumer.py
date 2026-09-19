"""RabbitMQ Consumer Runtime：幂等账本、重试队列与 DLQ。"""
import logging

import pika
from sqlalchemy import select

from app.config import settings
from app.domains.tasks.errors import EventHandler, PermanentTaskError
from app.domains.tasks.ledger import ConsumerLedger
from app.infrastructure.db import SessionLocal
from app.infrastructure.messaging.connection import connect, declare_topology
from app.infrastructure.messaging.envelope import (
    EnvelopeError,
    EventEnvelope,
    UnsupportedSchemaError,
    parse_envelope,
)
from app.infrastructure.models import ConsumerDelivery

logger = logging.getLogger(__name__)


class ConsumerRuntime:
    """单个消费循环：prefetch=1、手动 ack、处理结果与账本同事务。"""

    def __init__(self, consumer_name: str, handlers: dict[str, EventHandler]):
        self.consumer_name = consumer_name
        self.handlers = handlers
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    def start(self) -> None:
        self.connection = connect()
        self.channel = self.connection.channel()
        self.channel.basic_qos(prefetch_count=1)
        self.channel.confirm_delivery()
        declare_topology(self.channel)
        self.channel.basic_consume(
            queue=settings.rabbitmq_main_queue,
            on_message_callback=self._on_message,
        )
        logger.info("Consumer %s 开始消费 %s", self.consumer_name, settings.rabbitmq_main_queue)
        try:
            self.channel.start_consuming()
        finally:
            self._close()

    def stop(self) -> None:
        if self.channel is not None and not self.channel.is_closed:
            self.channel.stop_consuming()

    def _on_message(self, channel, method, properties, body) -> None:
        try:
            envelope = parse_envelope(body)
        except UnsupportedSchemaError as exc:
            logger.error("拒绝未知 schema 版本：%s", exc)
            self._dead_letter(body, method)
            return
        except EnvelopeError as exc:
            logger.error("非法事件：%s", exc)
            self._dead_letter(body, method)
            return

        db = SessionLocal()
        try:
            self._process(db, channel, method, body, envelope)
        finally:
            db.close()

    def _process(self, db, channel, method, body: bytes, envelope: EventEnvelope) -> None:
        ledger = ConsumerLedger(db, self.consumer_name)
        claim = ledger.claim(envelope.event_id)
        # 抢占记录独立提交：handler 失败回滚业务写入时账本行必须保留，
        # 否则后续租约过期重领与 DLQ 判定都失去依据
        db.commit()
        if not claim.claimed:
            if claim.reason == "already_succeeded":
                channel.basic_ack(delivery_tag=method.delivery_tag)
            else:
                # 其他消费者持有租约：延迟回重试队列，避免丢失或死循环
                if self._requeue_with_delay(body):
                    channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        try:
            handler = self.handlers.get(envelope.event_type)
            if handler is None:
                raise PermanentTaskError(f"没有注册的事件处理器：{envelope.event_type}")
            handler(db, envelope)
            ledger.complete(envelope.event_id)
            db.commit()
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as exc:
            db.rollback()
            self._handle_failure(db, channel, method, body, envelope, exc)

    def _handle_failure(self, db, channel, method, body: bytes, envelope: EventEnvelope, exc: Exception) -> None:
        delivery = db.scalar(
            select(ConsumerDelivery).where(
                ConsumerDelivery.consumer_name == self.consumer_name,
                ConsumerDelivery.event_id == envelope.event_id,
            )
        )
        attempt_no = delivery.attempt_no if delivery else 1
        error_code = exc.__class__.__name__
        error_summary = str(exc)[:500]
        logger.warning("事件处理失败（%s，尝试 %s）：%s", envelope.event_type, attempt_no, error_summary)

        permanent = isinstance(exc, PermanentTaskError) or attempt_no >= settings.message_max_attempts
        ledger = ConsumerLedger(db, self.consumer_name)
        if permanent:
            ledger.dead(envelope.event_id, error_code=error_code, error_summary=error_summary)
            db.commit()
            self._dead_letter(body, method)
        else:
            ledger.retry_wait(envelope.event_id, error_code=error_code, error_summary=error_summary)
            db.commit()
            if self._requeue_with_delay(body):
                channel.basic_ack(delivery_tag=method.delivery_tag)

    def _requeue_with_delay(self, body: bytes) -> bool:
        assert self.channel is not None, "consumer channel must be open"
        return self.channel.basic_publish(
            exchange=settings.rabbitmq_exchange,
            routing_key="retry",
            body=body,
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
        ) is not False

    def _dead_letter(self, body: bytes, method) -> bool:
        assert self.channel is not None, "consumer channel must be open"
        confirmed = self.channel.basic_publish(
            exchange="zhiyun.dlx",
            routing_key="dlq",
            body=body,
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
        ) is not False
        if confirmed:
            self.channel.basic_ack(delivery_tag=method.delivery_tag)
        return confirmed

    def _close(self) -> None:
        if self.channel is not None and not self.channel.is_closed:
            self.channel.close()
        if self.connection is not None and not self.connection.is_closed:
            self.connection.close()
        self.channel = None
        self.connection = None
