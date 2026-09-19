"""审计域：字段白名单与 PII 脱敏。"""
from collections.abc import Mapping

MASK = "***"

# 任何层级出现这些键名即视为敏感值，统一脱敏为 MASK
PII_KEYS = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "id_number",
        "address",
        "password",
        "password_hash",
        "token",
        "token_hash",
        "secret",
        "api_key",
        "private_key",
    }
)

# 审计允许保留的顶层字段；未列入的字段（含敏感键）不进入审计
DEFAULT_WHITELIST = frozenset(
    {
        "email",
        "status",
        "route",
        "score",
        "decision",
        "reason",
        "version",
        "org_id",
        "roles",
        "role",
        "title",
        "progress",
        "from_status",
        "to_status",
        "actor_id",
        "resource_type",
        "channel",
        "scope",
    }
)


def _redact_value(value, key: str):
    if key.casefold() in PII_KEYS:
        return MASK
    if isinstance(value, Mapping):
        return {k: _redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, key) for v in value]
    return value


def redact_diff(before, after, *, whitelist: frozenset[str] | None = None):
    """按白名单裁剪 before/after，并对保留值递归脱敏。

    返回值直接用于 audit_logs.before_json / after_json。
    """
    allowed = whitelist or DEFAULT_WHITELIST

    def clean(payload):
        if payload is None:
            return None
        return {k: _redact_value(v, k) for k, v in payload.items() if k in allowed}

    return clean(before), clean(after)
