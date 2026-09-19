"""持久化流事件：SSE 断线补发的权威事件源。"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import StreamEvent


def append(
    db: Session,
    *,
    org_id: str,
    stream_type: str,
    stream_id: str,
    event_type: str,
    data: dict,
) -> StreamEvent:
    event = StreamEvent(
        org_id=org_id,
        stream_type=stream_type,
        stream_id=stream_id,
        event_type=event_type,
        data_json=json.dumps(data, ensure_ascii=False, default=str),
    )
    db.add(event)
    db.flush()
    return event


def replay(
    db: Session,
    *,
    org_id: str,
    stream_id: str,
    after_sequence: int | None = None,
    limit: int = 500,
) -> list[StreamEvent]:
    stmt = (
        select(StreamEvent)
        .where(StreamEvent.org_id == org_id, StreamEvent.stream_id == stream_id)
        .order_by(StreamEvent.sequence)
        .limit(limit)
    )
    if after_sequence is not None:
        stmt = stmt.where(StreamEvent.sequence > after_sequence)
    return list(db.scalars(stmt).all())


def oldest_sequence(db: Session, *, org_id: str, stream_id: str) -> int | None:
    return db.scalar(
        select(StreamEvent.sequence)
        .where(StreamEvent.org_id == org_id, StreamEvent.stream_id == stream_id)
        .order_by(StreamEvent.sequence)
        .limit(1)
    )


def latest_sequence(db: Session, *, org_id: str, stream_id: str) -> int | None:
    return db.scalar(
        select(StreamEvent.sequence)
        .where(StreamEvent.org_id == org_id, StreamEvent.stream_id == stream_id)
        .order_by(StreamEvent.sequence.desc())
        .limit(1)
    )
