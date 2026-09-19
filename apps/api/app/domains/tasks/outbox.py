"""事务 Outbox：业务写入与事件同事务提交，Publisher 租约发布。"""
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.infrastructure.models import OutboxEvent, ensure_utc, now_utc


class OutboxService:
    def __init__(self, db: Session):
        self.db = db

    def enqueue(
        self,
        *,
        org_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        schema_version: int = 1,
    ) -> OutboxEvent:
        import json

        event = OutboxEvent(
            org_id=org_id,
            event_type=event_type,
            schema_version=schema_version,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
            status="PENDING",
            available_at=now_utc(),
        )
        self.db.add(event)
        return event

    def claim_batch(self, batch_size: int | None = None, lease_seconds: int | None = None) -> list[OutboxEvent]:
        """取回到期且未被租约占用的待发布事件，并写入租约。"""
        limit = batch_size or settings.outbox_batch_size
        lease = lease_seconds or settings.outbox_lease_seconds
        now = now_utc()
        events = list(
            self.db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "PENDING",
                    OutboxEvent.available_at <= now,
                    (OutboxEvent.lease_until.is_(None)) | (OutboxEvent.lease_until <= now),
                )
                .order_by(OutboxEvent.available_at)
                .limit(limit)
            ).all()
        )
        if events:
            self.db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_([event.id for event in events]))
                .values(lease_until=now + timedelta(seconds=lease))
            )
            self.db.commit()
        return events

    def mark_published(self, event_id: str) -> None:
        event = self.db.get(OutboxEvent, event_id)
        if event is None:
            return
        event.status = "PUBLISHED"
        event.published_at = now_utc()
        event.lease_until = None
        self.db.commit()

    def release_lease(self, event_id: str) -> None:
        event = self.db.get(OutboxEvent, event_id)
        if event is None:
            return
        event.lease_until = None
        self.db.commit()


def is_available(event: OutboxEvent, now=None) -> bool:
    now = now or now_utc()
    return (
        event.status == "PENDING"
        and ensure_utc(event.available_at) <= now
        and (event.lease_until is None or ensure_utc(event.lease_until) <= now)
    )
