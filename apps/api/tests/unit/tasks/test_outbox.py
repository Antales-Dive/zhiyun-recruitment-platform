"""Outbox 单元测试：入队、租约取批、发布标记与租约释放。"""
from datetime import timedelta

from app.domains.tasks.outbox import OutboxService, is_available
from app.infrastructure.models import OutboxEvent, now_utc
from sqlalchemy import select


def test_enqueue_creates_pending_event(db):
    outbox = OutboxService(db)
    event = outbox.enqueue(
        org_id="org-a",
        event_type="resume.parse.requested",
        aggregate_type="candidate",
        aggregate_id="cand-1",
        payload={"task_id": "t1"},
    )
    db.commit()

    assert event.status == "PENDING"
    assert event.schema_version == 1
    assert is_available(event)


def test_claim_batch_respects_lease(db):
    outbox = OutboxService(db)
    first = outbox.enqueue(org_id="org-a", event_type="e.1", aggregate_type="a", aggregate_id="x", payload={})
    second = outbox.enqueue(org_id="org-a", event_type="e.2", aggregate_type="a", aggregate_id="x", payload={})
    db.commit()

    batch = outbox.claim_batch(batch_size=1, lease_seconds=60)

    assert [event.id for event in batch] == [first.id]
    # 已租用事件在租约期内不再被取回；仅未租用的第二个事件可取
    second_batch = outbox.claim_batch(batch_size=10, lease_seconds=60)
    assert [event.id for event in second_batch] == [second.id]
    db.commit()
    assert outbox.claim_batch(batch_size=10, lease_seconds=60) == []


def test_claim_batch_reclaims_after_lease_expiry(db):
    outbox = OutboxService(db)
    event = outbox.enqueue(org_id="org-a", event_type="e.1", aggregate_type="a", aggregate_id="x", payload={})
    db.commit()
    outbox.claim_batch(batch_size=10, lease_seconds=1)
    db.commit()

    event.lease_until = now_utc() - timedelta(seconds=5)
    db.commit()

    batch = outbox.claim_batch(batch_size=10, lease_seconds=60)
    assert [item.id for item in batch] == [event.id]


def test_mark_published_sets_status_and_clears_lease(db):
    outbox = OutboxService(db)
    event = outbox.enqueue(org_id="org-a", event_type="e.1", aggregate_type="a", aggregate_id="x", payload={})
    db.commit()
    outbox.claim_batch(batch_size=10, lease_seconds=60)
    db.commit()

    outbox.mark_published(event.id)

    db.expire_all()
    published = db.get(OutboxEvent, event.id)
    assert published.status == "PUBLISHED"
    assert published.published_at is not None
    assert published.lease_until is None


def test_release_lease_allows_republish(db):
    outbox = OutboxService(db)
    event = outbox.enqueue(org_id="org-a", event_type="e.1", aggregate_type="a", aggregate_id="x", payload={})
    db.commit()
    outbox.claim_batch(batch_size=10, lease_seconds=60)
    db.commit()

    outbox.release_lease(event.id)

    batch = outbox.claim_batch(batch_size=10, lease_seconds=60)
    assert [item.id for item in batch] == [event.id]


def test_available_at_in_future_blocks_claim(db):
    outbox = OutboxService(db)
    outbox.enqueue(
        org_id="org-a",
        event_type="e.1",
        aggregate_type="a",
        aggregate_id="x",
        payload={},
    )
    db.commit()
    # 手动推迟可用时间，模拟退避事件
    event = db.scalar(select(OutboxEvent))
    event.available_at = now_utc() + timedelta(minutes=5)
    db.commit()

    assert outbox.claim_batch(batch_size=10, lease_seconds=60) == []
