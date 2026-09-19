"""认证依赖：Principal 只由服务端验证产生。

- 会话模式（默认）：从 Authorization: Bearer <token> 验证服务端会话；
- 请求头模拟模式：仅测试环境通过显式配置启用，生产启动即失败。
"""
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.domains.identity.principal import Principal
from app.domains.identity.service import get_session_principal
from app.infrastructure.db import get_db


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :].strip()
    return None


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    x_user_role: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    if settings.allow_header_auth:
        if not x_user_role or x_user_role not in settings.allowed_roles or not x_org_id:
            raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
        return Principal(
            user_id=None,
            org_id=x_org_id.strip(),
            roles=frozenset({x_user_role}),
            source="header",
        )

    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
    principal = get_session_principal(db, token=token)
    if principal is None:
        raise HTTPException(status_code=401, detail="TOKEN_EXPIRED")
    return principal


def require_role(role: str):
    def authorized(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(role):
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        return principal

    return authorized


def require_any_role(*roles: str):
    def authorized(principal: Principal = Depends(get_principal)) -> Principal:
        if not any(principal.has_role(role) for role in roles):
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        return principal

    return authorized
