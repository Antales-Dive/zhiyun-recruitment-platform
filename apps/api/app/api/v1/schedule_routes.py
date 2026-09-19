"""排期与通知路由：管理端时段/预约，公开端令牌预约。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import require_role
from app.api.envelope import success
from app.domains.identity.principal import Principal
from app.domains.scheduling.service import (
    SchedulingError,
    SlotConflictError,
    available_slots,
    cancel_reservation,
    create_schedule_invitation,
    create_slots,
    reserve_slot,
)
from app.infrastructure.db import get_db

router = APIRouter(prefix="/api/v1")


class SlotInterval(BaseModel):
    starts_at: str
    ends_at: str


class SlotCreatePayload(BaseModel):
    resource_type: str = Field(min_length=1, max_length=30)
    resource_id: str = Field(min_length=1, max_length=36)
    intervals: list[SlotInterval] = Field(min_length=1, max_length=50)


class InvitePayload(BaseModel):
    candidate_id: str
    expires_in_seconds: int = Field(default=7 * 24 * 3600, ge=300, le=90 * 24 * 3600)


@router.post("/schedule-slots", status_code=201)
def create_schedule_slots_route(
    payload: SlotCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        from datetime import datetime

        intervals = [
            (datetime.fromisoformat(item.starts_at), datetime.fromisoformat(item.ends_at))
            for item in payload.intervals
        ]
        slots = create_slots(
            db,
            org_id=principal.org_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            intervals=intervals,
        )
    except (SchedulingError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(
        request,
        {
            "slots": [
                {
                    "slot_id": slot.id,
                    "starts_at": slot.starts_at.isoformat(),
                    "ends_at": slot.ends_at.isoformat(),
                    "status": slot.status,
                }
                for slot in slots
            ]
        },
    )


@router.post("/schedule-invitations", status_code=201)
def create_schedule_invitation_route(
    payload: InvitePayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
):
    try:
        invitation, token = create_schedule_invitation(
            db,
            org_id=principal.org_id,
            candidate_id=payload.candidate_id,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except SchedulingError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(
        request,
        {"invitation_id": invitation.id, "token": token, "expires_at": invitation.expires_at.isoformat()},
    )


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation_route(
    reservation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("HR")),
    reason: str = "",
):
    try:
        reservation = cancel_reservation(
            db, reservation_id=reservation_id, org_id=principal.org_id, reason=reason or None
        )
    except SchedulingError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, {"reservation_id": reservation.id, "status": reservation.status})


public_router = APIRouter(prefix="/public")


@public_router.get("/schedules/{token}/slots")
def get_public_slots(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        slots = available_slots(db, token)
    except SchedulingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(
        request,
        {
            "slots": [
                {
                    "slot_id": slot.id,
                    "starts_at": slot.starts_at.isoformat(),
                    "ends_at": slot.ends_at.isoformat(),
                }
                for slot in slots
            ]
        },
    )


class ReservationPayload(BaseModel):
    slot_id: str
    client_request_id: str = Field(min_length=8, max_length=64)


@public_router.post("/schedules/{token}/reservations", status_code=201)
def create_public_reservation(
    token: str,
    payload: ReservationPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        reservation = reserve_slot(
            db, token=token, slot_id=payload.slot_id, client_request_id=payload.client_request_id
        )
    except SlotConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(request, {"reservation_id": reservation.id, "status": reservation.status})
