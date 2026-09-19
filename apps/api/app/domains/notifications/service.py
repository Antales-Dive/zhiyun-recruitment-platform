"""通知域：发送账本与 Provider 幂等（AC-007）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import Notification, NotificationDelivery, now_utc


class NotificationError(ValueError):
    pass


class ProviderNotConfiguredError(NotificationError):
    pass


def create_notification(
    db: Session, *, org_id: str, channel: str, template_version_id: str, recipient_ref: str, idempotency_key: str
) -> Notification:
    """以 (org, channel, idempotency_key) 幂等创建通知。"""
    existing = db.scalar(
        select(Notification).where(
            Notification.org_id == org_id,
            Notification.channel == channel,
            Notification.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    notification = Notification(
        org_id=org_id,
        channel=channel,
        template_version_id=template_version_id,
        recipient_ref=recipient_ref,
        status="PENDING",
        idempotency_key=idempotency_key,
    )
    db.add(notification)
    db.flush()
    return notification


def record_delivery(
    db: Session,
    *,
    notification_id: str,
    attempt_no: int,
    provider: str,
    provider_message_id: str | None,
    status: str,
) -> NotificationDelivery:
    delivery = NotificationDelivery(
        notification_id=notification_id,
        attempt_no=attempt_no,
        provider=provider,
        provider_message_id=provider_message_id,
        status=status,
    )
    db.add(delivery)
    return delivery


def has_succeeded_delivery(db: Session, notification_id: str) -> bool:
    delivery = db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.notification_id == notification_id,
            NotificationDelivery.status == "SENT",
        )
    )
    return delivery is not None


def mark_status(db: Session, notification: Notification, status: str) -> None:
    notification.status = status
    notification.updated_at = now_utc()
