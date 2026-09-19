"""Consumer Ledger 单元测试：抢占、幂等、租约过期与重试。"""
from datetime import timedelta

from app.domains.tasks.ledger import ConsumerLedger
from app.infrastructure.models import ConsumerDelivery, now_utc
from sqlalchemy import select


def test_first_claim_wins_with_attempt_one(db):
    ledger = ConsumerLedger(db, "worker-1")
    result = ledger.claim("event-1")
    db.commit()

    assert result.claimed is True
    assert result.attempt_no == 1
    assert result.reason == "claimed"


def test_duplicate_claim_with_active_lease_is_deferred(db):
    ledger = ConsumerLedger(db, "worker-1")
    ledger.claim("event-1")
    db.commit()

    second = ledger.claim("event-1")
    db.commit()

    assert second.claimed is False
    assert second.reason == "lease_active"


def test_completed_event_is_skipped(db):
    ledger = ConsumerLedger(db, "worker-1")
    ledger.claim("event-1")
    ledger.complete("event-1")
    db.commit()

    later = ConsumerLedger(db, "worker-1").claim("event-1")

    assert later.claimed is False
    assert later.reason == "already_succeeded"


def test_expired_lease_is_reclaimed_with_new_attempt(db):
    ledger = ConsumerLedger(db, "worker-1")
    ledger.claim("event-1", lease_seconds=60)
    db.commit()

    delivery = db.scalar(select(ConsumerDelivery))
    delivery.lease_until = now_utc() - timedelta(seconds=5)
    db.commit()

    reclaimed = ledger.claim("event-1", lease_seconds=60)

    assert reclaimed.claimed is True
    assert reclaimed.attempt_no == 2


def test_retry_wait_state_can_be_reclaimed(db):
    ledger = ConsumerLedger(db, "worker-1")
    ledger.claim("event-1")
    ledger.retry_wait("event-1", error_code="TIMEOUT", error_summary="超时")
    db.commit()

    reclaimed = ConsumerLedger(db, "worker-1").claim("event-1")

    assert reclaimed.claimed is True
    assert reclaimed.attempt_no == 2


def test_dead_state_can_be_replayed_with_new_attempt(db):
    ledger = ConsumerLedger(db, "worker-1")
    ledger.claim("event-1")
    ledger.dead("event-1", error_code="PERMANENT", error_summary="永久失败")
    db.commit()

    replayed = ConsumerLedger(db, "worker-1").claim("event-1")

    assert replayed.claimed is True
    assert replayed.attempt_no == 2
