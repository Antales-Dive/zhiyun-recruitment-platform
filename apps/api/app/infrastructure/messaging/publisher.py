"""Outbox Publisher：租约取批、publisher confirm 发布。"""
import logging

import pika

from app.config import settings
from app.infrastructure.messaging.connection import connect, declare_topology

logger = logging.getLogger(__name__)


class OutboxPublisher:
    """发布确认前崩溃可能重复投递，由 Consumer Ledger 幂等处理。"""

    def __init__(self):
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    def ensure_channel(self) -> pika.adapters.blocking_connection.BlockingChannel:
        if self.channel is None or self.channel.is_closed or self.connection is None or self.connection.is_closed:
            self.connection = connect()
            self.channel = self.connection.channel()
            # 确认模式必须在发布前启用：basic_publish 会阻塞等待 Broker Confirm
            self.channel.confirm_delivery()
            declare_topology(self.channel)
        return self.channel

    def publish(self, envelope: dict) -> bool:
        """发布单条消息；确认模式下 basic_publish 等待 Broker Confirm。"""
        import json

        channel = self.ensure_channel()
        try:
            channel.basic_publish(
                exchange=settings.rabbitmq_exchange,
                routing_key="#",
                body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
                properties=pika.BasicProperties(
                    delivery_mode=pika.DeliveryMode.Persistent,
                    content_type="application/json",
                    message_id=envelope["event_id"],
                ),
                mandatory=True,
            )
            return True
        except Exception:
            logger.exception("发布消息失败：%s", envelope["event_type"])
            self._reset_channel()
            return False

    def publish_batch(self, envelopes: list[dict]) -> list[bool]:
        results = []
        for envelope in envelopes:
            results.append(self.publish(envelope))
        return results

    def close(self) -> None:
        if self.channel is not None and not self.channel.is_closed:
            self.channel.close()
        if self.connection is not None and not self.connection.is_closed:
            self.connection.close()
        self.channel = None
        self.connection = None

    def _reset_channel(self) -> None:
        try:
            self.close()
        except Exception:
            self.channel = None
            self.connection = None
