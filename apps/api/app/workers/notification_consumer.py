"""通知 Consumer：消费 notification.requested，账本幂等外发（AC-007）。

失败状态（含 Provider 未配置）先提交账本再抛出，保证 notifications /
notification_deliveries 是发送事实源；重试/重放绝不重复产生外部副作用。
"""
from sqlalchemy.orm import Session

from app.domains.notifications.service import (
    create_notification,
    has_succeeded_delivery,
    mark_status,
    record_delivery,
)
from app.domains.tasks.errors import PermanentTaskError, RetryableTaskError
from app.infrastructure.email.port import EmailNotConfiguredError, EmailPort
from app.infrastructure.messaging.envelope import EventEnvelope


def handle_notification_requested(
    db: Session, envelope: EventEnvelope, email_provider: EmailPort | None = None
) -> None:
    payload = envelope.payload
    notification_key = str(payload.get("notification_id") or "")
    channel = payload.get("channel", "email")
    recipient = str(payload.get("recipient_ref") or "")
    template = str(payload.get("template_version_id", "generic-v1"))

    notification = create_notification(
        db,
        org_id=envelope.org_id,
        channel=channel,
        template_version_id=template,
        recipient_ref=recipient,
        idempotency_key=notification_key or envelope.event_id,
    )
    db.flush()

    if has_succeeded_delivery(db, notification.id):
        mark_status(db, notification, "SENT")
        db.commit()
        return

    if email_provider is None or not _provider_configured(email_provider):
        record_delivery(
            db, notification_id=notification.id, attempt_no=1,
            provider="email", provider_message_id=None, status="FAILED",
        )
        mark_status(db, notification, "FAILED")
        db.commit()
        raise PermanentTaskError("邮件 Provider 未配置（PD-001）")

    try:
        message_id = email_provider.send(
            to=recipient,
            subject="智聘云通知",
            body=f"模板 {template}",
            idempotency_key=notification.id,
        )
    except EmailNotConfiguredError as exc:
        record_delivery(
            db, notification_id=notification.id, attempt_no=1,
            provider="email", provider_message_id=None, status="FAILED",
        )
        mark_status(db, notification, "FAILED")
        db.commit()
        raise PermanentTaskError(str(exc)) from exc
    except Exception as exc:
        record_delivery(
            db, notification_id=notification.id, attempt_no=1,
            provider="email", provider_message_id=None, status="FAILED",
        )
        mark_status(db, notification, "RETRY_WAIT")
        db.commit()
        raise RetryableTaskError(str(exc)) from exc

    record_delivery(
        db, notification_id=notification.id, attempt_no=1,
        provider="email", provider_message_id=message_id, status="SENT",
    )
    mark_status(db, notification, "SENT")


def _provider_configured(provider: EmailPort) -> bool:
    return getattr(provider, "is_configured", lambda: True)()
