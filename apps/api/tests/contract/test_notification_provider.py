"""通知 Provider 合同测试：幂等外发、未配置显式失败（AC-007）。"""
import json

import pytest
from app.domains.notifications.service import (
    create_notification,
    has_succeeded_delivery,
)
from app.domains.tasks.errors import PermanentTaskError
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.email.port import EmailNotConfiguredError
from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
from app.workers.notification_consumer import handle_notification_requested


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


class RecordingEmailProvider:
    """合同桩：记录发送调用；按幂等键去重。"""

    def __init__(self):
        self.sent: list[dict] = []
        self._sent_keys: set[str] = set()

    def is_configured(self):
        return True

    def send(self, *, to, subject, body, idempotency_key):
        if idempotency_key in self._sent_keys:
            raise AssertionError("同一幂等键被重复发送")
        self._sent_keys.add(idempotency_key)
        self.sent.append({"to": to, "subject": subject, "idempotency_key": idempotency_key})
        return f"provider-msg-{len(self.sent)}"


class UnconfiguredEmailProvider:
    def is_configured(self):
        return False

    def send(self, **kwargs):
        raise EmailNotConfiguredError("未配置")


def envelope_for(payload: dict, event_id: str = "evt-notify"):
    return parse_envelope(
        json.dumps(
            build_envelope(
                event_id=event_id,
                event_type="notification.requested",
                aggregate_type="notification",
                aggregate_id=payload.get("notification_id", "n-1"),
                org_id="org-a",
                trace_id="test",
                payload=payload,
            )
        )
    )


class TestNotificationConsumer:
    def test_sends_once_and_records_delivery(self, db):
        provider = RecordingEmailProvider()
        envelope = envelope_for(
            {"notification_id": "n-send-1", "channel": "email", "recipient_ref": "cand@example.com"}
        )

        handle_notification_requested(db, envelope, email_provider=provider)
        db.commit()

        from app.infrastructure.models import Notification, NotificationDelivery

        notification = db.query(Notification).filter_by(idempotency_key="n-send-1").one()
        assert notification.status == "SENT"
        delivery = db.query(NotificationDelivery).filter_by(notification_id=notification.id).one()
        assert delivery.status == "SENT"
        assert delivery.provider_message_id.startswith("provider-msg-")
        assert len(provider.sent) == 1
        assert provider.sent[0]["to"] == "cand@example.com"

    def test_duplicate_event_does_not_resend(self, db):
        provider = RecordingEmailProvider()
        envelope = envelope_for(
            {"notification_id": "n-dup-1", "channel": "email", "recipient_ref": "dup@example.com"}
        )

        handle_notification_requested(db, envelope, email_provider=provider)
        db.commit()
        # 模拟重复投递（confirm 丢失后重发）
        handle_notification_requested(db, envelope, email_provider=provider)
        db.commit()

        assert len(provider.sent) == 1
        from app.infrastructure.models import Notification, NotificationDelivery

        notification = db.query(Notification).filter_by(idempotency_key="n-dup-1").one()
        deliveries = (
            db.query(NotificationDelivery).filter_by(notification_id=notification.id).count()
        )
        assert deliveries == 1

    def test_unconfigured_provider_fails_explicitly(self, db):
        envelope = envelope_for(
            {"notification_id": "n-unconf-1", "channel": "email", "recipient_ref": "x@example.com"}
        )

        with pytest.raises(PermanentTaskError):
            handle_notification_requested(db, envelope, email_provider=None)

        from app.infrastructure.models import Notification, NotificationDelivery

        notification = db.query(Notification).filter_by(idempotency_key="n-unconf-1").one()
        assert notification.status == "FAILED"
        delivery = db.query(NotificationDelivery).filter_by(notification_id=notification.id).one()
        assert delivery.status == "FAILED"

    def test_create_notification_is_idempotent(self, db):
        first = create_notification(
            db, org_id="org-a", channel="email", template_version_id="t1",
            recipient_ref="a@b.c", idempotency_key="key-idem",
        )
        db.flush()
        second = create_notification(
            db, org_id="org-a", channel="email", template_version_id="t1",
            recipient_ref="a@b.c", idempotency_key="key-idem",
        )
        assert second.id == first.id

    def test_has_succeeded_delivery_guards_retry(self, db):
        provider = RecordingEmailProvider()
        envelope = envelope_for(
            {"notification_id": "n-guard-1", "channel": "email", "recipient_ref": "g@example.com"}
        )
        handle_notification_requested(db, envelope, email_provider=provider)
        db.commit()

        from app.infrastructure.models import Notification

        notification = db.query(Notification).filter_by(idempotency_key="n-guard-1").one()
        assert has_succeeded_delivery(db, notification.id) is True
