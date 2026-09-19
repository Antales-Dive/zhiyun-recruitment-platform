"""排期域：时段、公开令牌与并发安全预约（AC-008）。"""
import hashlib
import json
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.tasks.idempotency import IdempotencyConflict, begin, complete, request_digest
from app.domains.tasks.outbox import OutboxService
from app.infrastructure.models import (
    Candidate,
    IdempotencyRecord,
    Reservation,
    ScheduleInvitation,
    ScheduleSlot,
    ensure_utc,
    now_utc,
)


class SchedulingError(ValueError):
    pass


class SlotConflictError(SchedulingError):
    pass


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_slots(
    db: Session, *, org_id: str, resource_type: str, resource_id: str, intervals: list[tuple]
) -> list[ScheduleSlot]:
    """创建可用时段；校验区间合法且不与既有时段重叠。"""
    slots = []
    for starts_at, ends_at in intervals:
        if ends_at <= starts_at:
            raise SchedulingError("INVALID_INTERVAL")
        overlap = db.scalar(
            select(ScheduleSlot.id).where(
                ScheduleSlot.org_id == org_id,
                ScheduleSlot.resource_id == resource_id,
                ScheduleSlot.status == "AVAILABLE",
                ScheduleSlot.starts_at < ends_at,
                ScheduleSlot.ends_at > starts_at,
            )
        )
        if overlap is not None:
            raise SchedulingError("SLOT_OVERLAP")
        slot = ScheduleSlot(
            org_id=org_id,
            resource_type=resource_type,
            resource_id=resource_id,
            starts_at=starts_at,
            ends_at=ends_at,
            status="AVAILABLE",
        )
        db.add(slot)
        slots.append(slot)
    db.commit()
    return slots


def create_schedule_invitation(
    db: Session, *, org_id: str, candidate_id: str, expires_in_seconds: int = 7 * 24 * 3600
) -> tuple[ScheduleInvitation, str]:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.org_id != org_id:
        raise SchedulingError("CANDIDATE_NOT_FOUND")
    token = secrets.token_urlsafe(32)
    invitation = ScheduleInvitation(
        org_id=org_id,
        candidate_id=candidate.id,
        token_hash=_token_hash(token),
        status="PENDING",
        expires_at=now_utc() + timedelta(seconds=expires_in_seconds),
    )
    db.add(invitation)
    db.commit()
    return invitation, token


def available_slots(db: Session, token: str) -> list[ScheduleSlot]:
    invitation = db.scalar(
        select(ScheduleInvitation).where(ScheduleInvitation.token_hash == _token_hash(token))
    )
    if invitation is None:
        raise SchedulingError("INVITATION_NOT_FOUND")
    if ensure_utc(invitation.expires_at) <= now_utc():
        raise SchedulingError("INVITATION_EXPIRED")
    if invitation.status != "PENDING":
        raise SchedulingError("INVITATION_USED")
    slots = db.scalars(
        select(ScheduleSlot)
        .where(
            ScheduleSlot.org_id == invitation.org_id,
            ScheduleSlot.status == "AVAILABLE",
            ScheduleSlot.starts_at > now_utc(),
        )
        .order_by(ScheduleSlot.starts_at)
    ).all()
    return list(slots)


def reserve_slot(db: Session, *, token: str, slot_id: str, client_request_id: str) -> Reservation:
    """预约：数据库唯一约束作为最终防线；冲突映射为 409（AC-008）。"""
    invitation = db.scalar(
        select(ScheduleInvitation).where(ScheduleInvitation.token_hash == _token_hash(token))
    )
    if invitation is None:
        raise SchedulingError("INVITATION_NOT_FOUND")
    if ensure_utc(invitation.expires_at) <= now_utc():
        raise SchedulingError("INVITATION_EXPIRED")
    try:
        existing_request = begin(
            db,
            org_id=invitation.org_id,
            actor_id=invitation.candidate_id,
            operation="schedule.reserve",
            key=client_request_id,
            request_hash=request_digest(f"{invitation.id}:{slot_id}"),
        )
    except IdempotencyConflict as exc:
        raise SchedulingError("IDEMPOTENCY_CONFLICT") from exc
    if existing_request is not None:
        response = json.loads(existing_request.response_json or "{}")
        reservation = db.get(Reservation, response.get("reservation_id"))
        if reservation is None:
            raise SchedulingError("RESERVATION_RESULT_UNAVAILABLE")
        return reservation
    if invitation.status != "PENDING":
        db.rollback()
        raise SchedulingError("INVITATION_USED")

    slot = db.scalar(
        select(ScheduleSlot).where(
            ScheduleSlot.id == slot_id,
            ScheduleSlot.org_id == invitation.org_id,
            ScheduleSlot.status == "AVAILABLE",
        )
    )
    if slot is None:
        db.rollback()
        raise SchedulingError("SLOT_NOT_AVAILABLE")

    reservation = Reservation(
        slot_id=slot.id,
        candidate_id=invitation.candidate_id,
        status="ACTIVE",
        active_slot_key=slot.id,
    )
    db.add(reservation)
    slot.status = "RESERVED"
    invitation.status = "USED"
    try:
        db.flush()  # 唯一约束在 flush 即触发
        OutboxService(db).enqueue(
            org_id=invitation.org_id,
            event_type="reservation.created",
            aggregate_type="reservation",
            aggregate_id=reservation.id,
            payload={
                "reservation_id": reservation.id,
                "starts_at": slot.starts_at.isoformat(),
                "ends_at": slot.ends_at.isoformat(),
            },
        )
        idempotency_record = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.org_id == invitation.org_id,
                IdempotencyRecord.actor_id == invitation.candidate_id,
                IdempotencyRecord.operation == "schedule.reserve",
                IdempotencyRecord.key == client_request_id,
            )
        )
        if idempotency_record is None:
            raise SchedulingError("IDEMPOTENCY_RECORD_MISSING")
        complete(
            db,
            idempotency_record,
            response_status=201,
            response_json={"reservation_id": reservation.id},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise SlotConflictError("SLOT_ALREADY_RESERVED") from exc
    return reservation


def cancel_reservation(
    db: Session, *, reservation_id: str, org_id: str, reason: str | None = None
) -> Reservation:
    reservation = db.scalar(
        select(Reservation).join(ScheduleSlot, ScheduleSlot.id == Reservation.slot_id).where(
            Reservation.id == reservation_id,
            ScheduleSlot.org_id == org_id,
        )
    )
    if reservation is None:
        raise SchedulingError("RESERVATION_NOT_FOUND")
    if reservation.status != "ACTIVE":
        return reservation
    reservation.status = "CANCELLED"
    reservation.active_slot_key = None  # 释放唯一约束，允许再次预约
    reservation.row_version += 1
    slot = db.get(ScheduleSlot, reservation.slot_id)
    if slot is not None:
        slot.status = "AVAILABLE"
    OutboxService(db).enqueue(
        org_id=org_id,
        event_type="reservation.cancelled",
        aggregate_type="reservation",
        aggregate_id=reservation.id,
        payload={"reservation_id": reservation.id, "reason_code": reason or "CANCELLED"},
    )
    db.commit()
    return reservation
