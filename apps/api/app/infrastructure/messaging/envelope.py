"""统一事件 envelope（04-contracts-and-data.md §6）。"""
import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

SUPPORTED_SCHEMA_VERSION = 1


class EnvelopeError(ValueError):
    pass


class UnsupportedSchemaError(EnvelopeError):
    pass


class EventEnvelope(BaseModel):
    event_id: str
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: str
    org_id: str
    occurred_at: str
    trace_id: str
    causation_id: str | None = None
    payload: dict = Field(default_factory=dict)


def build_envelope(
    *,
    event_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    org_id: str,
    trace_id: str,
    causation_id: str | None = None,
    payload: dict | None = None,
    schema_version: int = SUPPORTED_SCHEMA_VERSION,
) -> dict:
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        org_id=org_id,
        occurred_at=datetime.now(UTC).isoformat(),
        trace_id=trace_id,
        causation_id=causation_id,
        payload=payload or {},
    ).model_dump()


def parse_envelope(raw: bytes | str) -> EventEnvelope:
    try:
        data = json.loads(raw) if isinstance(raw, bytes) else json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvelopeError("事件不是合法 JSON") from exc
    try:
        envelope = EventEnvelope.model_validate(data)
    except Exception as exc:
        raise EnvelopeError("事件缺少必需字段") from exc
    if envelope.schema_version > SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"事件 schema 主版本 {envelope.schema_version} 高于支持版本 {SUPPORTED_SCHEMA_VERSION}"
        )
    return envelope
