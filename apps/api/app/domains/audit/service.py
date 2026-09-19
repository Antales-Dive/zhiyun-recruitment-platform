"""审计域：不可变审计记录写入服务。"""
import json
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.domains.audit.redaction import redact_diff
from app.infrastructure.models import AuditLog


def _to_json(payload) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, default=str)


class AuditService:
    """只追加审计：仅提供 record，不提供修改或删除入口。"""

    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        *,
        org_id: str,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        before: Mapping | None = None,
        after: Mapping | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> AuditLog:
        redacted_before, redacted_after = redact_diff(before, after)
        log = AuditLog(
            org_id=org_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_json=_to_json(redacted_before),
            after_json=_to_json(redacted_after),
            reason=reason,
            trace_id=trace_id,
        )
        self.db.add(log)
        return log
