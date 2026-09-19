"""幂等记录：相同键相同请求摘要返回原结果，不同摘要返回冲突。"""
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.models import IdempotencyRecord


class IdempotencyConflict(Exception):
    pass


def request_digest(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return sha256(payload).hexdigest()


def begin(
    db: Session,
    *,
    org_id: str,
    actor_id: str | None,
    operation: str,
    key: str,
    request_hash: str,
) -> IdempotencyRecord | None:
    """返回 None 表示可执行新请求；返回已有记录表示应复用原结果。"""
    existing = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.org_id == org_id,
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict("相同幂等键但请求摘要不同")
        return existing

    record = IdempotencyRecord(
        org_id=org_id,
        actor_id=actor_id,
        operation=operation,
        key=key,
        request_hash=request_hash,
        response_status=0,
        response_json="{}",
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError as exc:
        # 并发相同键：回滚后读取既有记录并裁决
        db.rollback()
        existing = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.org_id == org_id,
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == key,
            )
        )
        if existing is None:
            raise AssertionError("幂等记录并发插入后不可见") from exc
        if existing.request_hash != request_hash:
            raise IdempotencyConflict("相同幂等键但请求摘要不同") from exc
        return existing
    return None


def complete(
    db: Session,
    record: IdempotencyRecord,
    *,
    response_status: int,
    response_json: dict,
) -> None:
    import json

    record.response_status = response_status
    record.response_json = json.dumps(response_json, ensure_ascii=False, default=str)
