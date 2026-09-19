"""Consumer Ledger：以 (consumer_name, event_id) 幂等消费并记录尝试。"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.infrastructure.models import ConsumerDelivery, ensure_utc, now_utc


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    attempt_no: int
    reason: str  # claimed | already_succeeded | lease_active


class ConsumerLedger:
    def __init__(self, db: Session, consumer_name: str):
        self.db = db
        self.consumer_name = consumer_name

    def claim(self, event_id: str, lease_seconds: int | None = None) -> ClaimResult:
        """抢占事件处理权；处理结果与业务写入同事务提交。"""
        lease = lease_seconds or settings.consumer_lease_seconds
        now = now_utc()
        delivery = self.db.scalar(
            select(ConsumerDelivery).where(
                ConsumerDelivery.consumer_name == self.consumer_name,
                ConsumerDelivery.event_id == event_id,
            )
        )
        if delivery is None:
            self.db.add(
                ConsumerDelivery(
                    consumer_name=self.consumer_name,
                    event_id=event_id,
                    status="PROCESSING",
                    attempt_no=1,
                    lease_until=now + timedelta(seconds=lease),
                )
            )
            self.db.flush()
            return ClaimResult(claimed=True, attempt_no=1, reason="claimed")

        if delivery.status == "SUCCEEDED":
            return ClaimResult(claimed=False, attempt_no=delivery.attempt_no, reason="already_succeeded")

        if (
            delivery.status == "PROCESSING"
            and delivery.lease_until is not None
            and ensure_utc(delivery.lease_until) > now
        ):
            return ClaimResult(claimed=False, attempt_no=delivery.attempt_no, reason="lease_active")

        # 租约过期或处于 RETRY_WAIT/DEAD 可重新尝试
        delivery.status = "PROCESSING"
        delivery.attempt_no += 1
        delivery.lease_until = now + timedelta(seconds=lease)
        delivery.completed_at = None
        return ClaimResult(claimed=True, attempt_no=delivery.attempt_no, reason="claimed")

    def complete(self, event_id: str) -> None:
        delivery = self.db.scalar(
            select(ConsumerDelivery).where(
                ConsumerDelivery.consumer_name == self.consumer_name,
                ConsumerDelivery.event_id == event_id,
            )
        )
        if delivery is None:
            return
        delivery.status = "SUCCEEDED"
        delivery.completed_at = now_utc()
        delivery.lease_until = None

    def retry_wait(self, event_id: str, *, error_code: str, error_summary: str | None) -> None:
        delivery = self._require(event_id)
        delivery.status = "RETRY_WAIT"
        delivery.completed_at = None
        delivery.lease_until = None
        delivery.error_code = error_code
        delivery.error_summary = (error_summary or "")[:500]

    def dead(self, event_id: str, *, error_code: str, error_summary: str | None) -> None:
        delivery = self._require(event_id)
        delivery.status = "DEAD"
        delivery.completed_at = now_utc()
        delivery.lease_until = None
        delivery.error_code = error_code
        delivery.error_summary = (error_summary or "")[:500]

    def _require(self, event_id: str) -> ConsumerDelivery:
        delivery = self.db.scalar(
            select(ConsumerDelivery).where(
                ConsumerDelivery.consumer_name == self.consumer_name,
                ConsumerDelivery.event_id == event_id,
            )
        )
        assert delivery is not None, "ledger delivery must exist before status update"
        return delivery
