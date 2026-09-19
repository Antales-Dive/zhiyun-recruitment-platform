"""认证路由：登录、登出、当前身份。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_principal
from app.api.envelope import success
from app.domains.identity.principal import Principal
from app.domains.identity.service import (
    IdentityError,
    RateLimitedError,
    login,
    logout,
)
from app.infrastructure.db import get_db

router = APIRouter(prefix="/api/v1/auth")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)
    org_id: str | None = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    token: str
    user_id: str
    org_id: str
    roles: list[str]
    expires_at: str


@router.post("/login")
def login_route(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    try:
        result = login(
            db,
            email=payload.email,
            password=payload.password,
            org_id=payload.org_id,
            trace_id=request.state.trace_id,
        )
        db.commit()
    except RateLimitedError as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail="RATE_LIMITED") from exc
    except IdentityError as exc:
        # 失败审计（AUTH_LOGIN_FAILED）必须持久化，不能随失败请求回滚
        db.commit()
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS") from exc
    return success(
        request,
        LoginResponse(
            token=result.token,
            user_id=result.user_id,
            org_id=result.org_id,
            roles=result.roles,
            expires_at=result.expires_at.isoformat(),
        ).model_dump(),
    )


@router.post("/logout")
def logout_route(
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if principal.session_id:
        logout(
            db,
            session_id=principal.session_id,
            org_id=principal.org_id,
            actor_id=principal.user_id,
            trace_id=request.state.trace_id,
        )
        db.commit()
    return success(request, {"logged_out": True})


@router.get("/me")
def me_route(
    request: Request,
    principal: Principal = Depends(get_principal),
):
    return success(
        request,
        {
            "user_id": principal.user_id,
            "org_id": principal.org_id,
            "roles": sorted(principal.roles),
            "session_id": principal.session_id,
        },
    )
