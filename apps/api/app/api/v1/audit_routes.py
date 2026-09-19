"""审计路由：仅 ADMIN / AUDITOR 可查询本组织审计记录。"""
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_any_role
from app.api.envelope import success
from app.domains.identity.principal import Principal
from app.infrastructure.db import get_db
from app.infrastructure.models import AuditLog

router = APIRouter(prefix="/api/v1")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def _encode_cursor(row: AuditLog) -> str:
    # 使用与数据库存储一致的 DATETIME 文本格式（无 T/时区后缀），
    # 保证 SQLite/MySQL 字符串比较与索引键序一致
    payload = json.dumps([row.created_at.strftime("%Y-%m-%d %H:%M:%S.%f"), row.id])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at, row_id = json.loads(raw)
        return created_at, str(row_id)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="INVALID_CURSOR") from None


def _serialize(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "actor_id": row.actor_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "before": json.loads(row.before_json) if row.before_json else None,
        "after": json.loads(row.after_json) if row.after_json else None,
        "reason": row.reason,
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/audit")
def list_audit(
    request: Request,
    principal: Principal = Depends(require_any_role("ADMIN", "AUDITOR")),
    db: Session = Depends(get_db),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
):
    stmt = (
        select(AuditLog)
        .where(AuditLog.org_id == principal.org_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )
    if cursor:
        created_at, row_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (AuditLog.created_at < created_at)
            | ((AuditLog.created_at == created_at) & (AuditLog.id < row_id))
        )
    rows = list(db.scalars(stmt.limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    return success(
        request,
        {
            "items": [_serialize(row) for row in rows],
            "next_cursor": next_cursor,
        },
    )
