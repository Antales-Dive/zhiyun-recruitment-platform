"""RabbitMQ 连接管理：带重试的阻塞连接与拓扑声明。"""
import logging
import time

import pika
from pika.exceptions import AMQPConnectionError

from app.config import settings

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 2.0


def connect(url: str | None = None, max_attempts: int = 5) -> pika.BlockingConnection:
    """建立连接；Broker 不可用时按指数退避重试。"""
    attempt = 0
    while True:
        attempt += 1
        try:
            parameters = pika.URLParameters(url or settings.rabbitmq_url)
            parameters.socket_timeout = 10
            return pika.BlockingConnection(parameters)
        except AMQPConnectionError:
            if attempt >= max_attempts:
                raise
            logger.warning("RabbitMQ 连接失败（第 %s 次），%s 秒后重试", attempt, RETRY_BACKOFF_SECONDS)
            time.sleep(RETRY_BACKOFF_SECONDS)


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    """声明持久化 Exchange、业务队列、重试队列与 DLQ。"""
    exchange = settings.rabbitmq_exchange
    channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="zhiyun.dlx", exchange_type="direct", durable=True)

    # 主队列：处理失败的消息死信到 DLX
    channel.queue_declare(
        queue=settings.rabbitmq_main_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "zhiyun.dlx",
            "x-dead-letter-routing-key": "dlq",
        },
    )
    channel.queue_bind(queue=settings.rabbitmq_main_queue, exchange=exchange, routing_key="#")

    # 重试队列：TTL 到期后按原路由键重回主 Exchange
    channel.queue_declare(
        queue=settings.rabbitmq_retry_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": exchange,
            "x-message-ttl": settings.message_retry_delay_ms,
        },
    )
    channel.queue_bind(queue=settings.rabbitmq_retry_queue, exchange=exchange, routing_key="retry")

    # 死信队列
    channel.queue_declare(queue=settings.rabbitmq_dlq, durable=True)
    channel.queue_bind(queue=settings.rabbitmq_dlq, exchange="zhiyun.dlx", routing_key="dlq")
