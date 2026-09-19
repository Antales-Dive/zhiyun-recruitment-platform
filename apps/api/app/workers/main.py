"""Worker 主进程：Outbox Publisher + RabbitMQ Consumer，支持优雅停机。

启动两个线程：
- publisher：租约取批 outbox 事件，publisher confirm 后标记 PUBLISHED；
- consumer：按 (consumer_name, event_id) 幂等处理业务事件。

收到 SIGINT/SIGTERM 后停止拉取、完成当前处理、释放连接后退出。
"""
import json
import logging
import signal
import threading
from collections.abc import Callable

from app.config import settings
from app.domains.tasks.outbox import OutboxService
from app.infrastructure.db import SessionLocal
from app.infrastructure.messaging.consumer import ConsumerRuntime
from app.infrastructure.messaging.envelope import build_envelope
from app.infrastructure.messaging.publisher import OutboxPublisher

logger = logging.getLogger(__name__)

# 各领域在此注册事件处理器（TASK-004/006/008/009/010 持续扩展）
from app.workers.knowledge_worker import handle_knowledge_ingestion_requested  # noqa: E402
from app.workers.matching_consumer import handle_analysis_requested  # noqa: E402
from app.workers.notification_consumer import handle_notification_requested  # noqa: E402
from app.workers.resume_worker import handle_resume_parse_requested  # noqa: E402

HANDLERS: dict[str, Callable] = {
    "resume.parse.requested": handle_resume_parse_requested,
    "analysis.requested": handle_analysis_requested,
    "knowledge.ingestion.requested": handle_knowledge_ingestion_requested,
    "notification.requested": handle_notification_requested,
}

PUBLISH_POLL_SECONDS = 1.0


def publish_outbox_events(stop_event: threading.Event) -> None:
    publisher = OutboxPublisher()
    try:
        while not stop_event.is_set():
            db = SessionLocal()
            try:
                outbox = OutboxService(db)
                events = outbox.claim_batch()
                if not events:
                    stop_event.wait(PUBLISH_POLL_SECONDS)
                    continue
                envelopes = [
                    build_envelope(
                        event_id=event.id,
                        event_type=event.event_type,
                        aggregate_type=event.aggregate_type,
                        aggregate_id=event.aggregate_id,
                        org_id=event.org_id,
                        trace_id="worker",
                        payload=json.loads(event.payload_json or "{}"),
                        schema_version=event.schema_version,
                    )
                    for event in events
                ]
                confirmed = publisher.publish_batch(envelopes)
                for event, ok in zip(events, confirmed, strict=False):
                    if ok:
                        outbox.mark_published(event.id)
                    else:
                        outbox.release_lease(event.id)
            except Exception:
                logger.exception("Outbox 发布循环异常")
                stop_event.wait(PUBLISH_POLL_SECONDS)
            finally:
                db.close()
    finally:
        publisher.close()
        logger.info("Outbox Publisher 已停止")


def consume_events(stop_event: threading.Event) -> None:
    runtime = ConsumerRuntime(consumer_name="zhiyun-worker", handlers=HANDLERS)

    def request_stop(*_):
        runtime.stop()

    try:
        runtime.start()
    except Exception:
        logger.exception("Consumer 启动或运行失败")
        runtime.stop()


def run_worker() -> None:
    logging.basicConfig(level=settings.log_level)
    stop_event = threading.Event()

    def signal_handler(*_):
        logger.info("收到停止信号，开始优雅停机")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    publisher_thread = threading.Thread(
        target=publish_outbox_events, args=(stop_event,), name="outbox-publisher", daemon=True
    )
    consumer_thread = threading.Thread(
        target=consume_events, args=(stop_event,), name="event-consumer", daemon=True
    )
    publisher_thread.start()
    consumer_thread.start()
    try:
        while publisher_thread.is_alive() or consumer_thread.is_alive():
            publisher_thread.join(timeout=1)
            consumer_thread.join(timeout=1)
    except KeyboardInterrupt:
        signal_handler()
    logger.info("Worker 已退出")


if __name__ == "__main__":
    run_worker()
