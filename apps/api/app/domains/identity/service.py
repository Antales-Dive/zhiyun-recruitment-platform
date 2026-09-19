"""身份域：组织、用户、角色、会话、登录限流与资源级访问策略。

所有写操作与审计同事务提交；调用方负责 commit。会话令牌只存 SHA-256
哈希，明文令牌只在登录响应中出现一次。
"""
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domains.audit.service import AuditService
from app.domains.identity.password import hash_password, verify_password
from app.domains.identity.principal import Principal
from app.infrastructure.models import (
    Candidate,
    CandidateAssignment,
    Organization,
    Role,
    RoleBinding,
    User,
    UserSession,
    ensure_utc,
    now_utc,
)


class IdentityError(Exception):
    pass


class InvalidCredentialsError(IdentityError):
    pass


class RateLimitedError(IdentityError):
    pass


class UserNotFoundError(IdentityError):
    pass


class EmailTakenError(IdentityError):
    pass


class RoleNotFoundError(IdentityError):
    pass


@dataclass(frozen=True)
class LoginResult:
    token: str
    user_id: str
    org_id: str
    roles: list[str]
    expires_at: datetime


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


class LoginRateLimiter:
    """进程内固定窗口限流；Redis 化随 TASK-003 引入。"""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            self._prune(key)
            return len(self._failures.get(key, [])) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key)
            self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset_all(self) -> None:
        """清空全部限流状态（测试与运维排障用）。"""
        with self._lock:
            self._failures.clear()

    def _prune(self, key: str) -> None:
        cutoff = time.monotonic() - self.window_seconds
        self._failures[key] = [t for t in self._failures.get(key, []) if t > cutoff]


rate_limiter = LoginRateLimiter(
    max_attempts=settings.login_rate_limit_max,
    window_seconds=settings.login_rate_limit_window_seconds,
)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def create_organization(db: Session, *, org_id: str | None = None, name: str) -> Organization:
    if org_id:
        existing = db.get(Organization, org_id)
        if existing is not None:
            return existing
    org = Organization(id=org_id or str(uuid4()), name=name.strip(), status="ACTIVE")
    db.add(org)
    db.flush()
    return org


def get_or_create_default_org(db: Session) -> Organization:
    org = db.get(Organization, settings.default_org_id)
    if org is None:
        org = create_organization(db, org_id=settings.default_org_id, name="默认组织")
    return org


def ensure_roles(db: Session) -> list[Role]:
    """幂等补齐固定角色；迁移与测试共用，避免重复种子。"""
    existing = {role.code for role in db.scalars(select(Role)).all()}
    roles: list[Role] = []
    for code in settings.allowed_roles:
        if code not in existing:
            role = Role(code=code)
            db.add(role)
            roles.append(role)
    return roles


def create_user(db: Session, *, org_id: str, email: str, password: str) -> User:
    normalized = normalize_email(email)
    existing = db.scalar(select(User).where(User.org_id == org_id, User.email == normalized))
    if existing is not None:
        raise EmailTakenError("该组织下邮箱已存在")
    user = User(
        org_id=org_id,
        email=normalized,
        password_hash=hash_password(password),
        status="ACTIVE",
    )
    db.add(user)
    db.flush()
    return user


def get_role(db: Session, code: str) -> Role:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise RoleNotFoundError(f"角色 {code} 不存在")
    return role


def assign_role(
    db: Session,
    *,
    user_id: str,
    org_id: str,
    role_code: str,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> RoleBinding:
    role = get_role(db, role_code)
    binding = RoleBinding(user_id=user_id, org_id=org_id, role_id=role.id)
    db.add(binding)
    audit = AuditService(db)
    audit.record(
        org_id=org_id,
        actor_id=actor_id or user_id,
        action="ROLE_ASSIGNED",
        resource_type="user",
        resource_id=user_id,
        after={"roles": [role_code]},
        reason="角色绑定",
        trace_id=trace_id,
    )
    return binding


def user_roles(db: Session, user_id: str, org_id: str) -> list[str]:
    rows = db.execute(
        select(Role.code)
        .join(RoleBinding, RoleBinding.role_id == Role.id)
        .where(RoleBinding.user_id == user_id, RoleBinding.org_id == org_id)
    ).scalars()
    return list(rows)


def login(
    db: Session,
    *,
    email: str,
    password: str,
    org_id: str | None = None,
    trace_id: str | None = None,
) -> LoginResult:
    normalized = normalize_email(email)
    audit = AuditService(db)

    if rate_limiter.is_blocked(normalized):
        raise RateLimitedError("登录失败次数过多")

    user = _find_user(db, normalized, org_id)
    if user is None or not verify_password(password, user.password_hash) or user.status != "ACTIVE":
        rate_limiter.record_failure(normalized)
        audit.record(
            org_id=user.org_id if user else settings.default_org_id,
            actor_id=None,
            action="AUTH_LOGIN_FAILED",
            resource_type="user",
            resource_id=user.id if user else None,
            reason="INVALID_CREDENTIALS",
            trace_id=trace_id,
        )
        raise InvalidCredentialsError("邮箱或密码错误")

    rate_limiter.reset(normalized)
    token = secrets.token_urlsafe(32)
    expires_at = now_utc() + timedelta(seconds=settings.session_ttl_seconds)
    session = UserSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires_at)
    db.add(session)
    db.flush()
    roles = user_roles(db, user.id, user.org_id)
    audit.record(
        org_id=user.org_id,
        actor_id=user.id,
        action="AUTH_LOGIN",
        resource_type="session",
        resource_id=session.id,
        after={"org_id": user.org_id, "roles": roles},
        trace_id=trace_id,
    )
    return LoginResult(
        token=token,
        user_id=user.id,
        org_id=user.org_id,
        roles=roles,
        expires_at=expires_at,
    )


def logout(
    db: Session,
    *,
    session_id: str,
    org_id: str,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    session = db.get(UserSession, session_id)
    if session is None or session.revoked_at is not None:
        return
    session.revoked_at = now_utc()
    audit = AuditService(db)
    audit.record(
        org_id=org_id,
        actor_id=actor_id,
        action="AUTH_LOGOUT",
        resource_type="session",
        resource_id=session.id,
        trace_id=trace_id,
    )


def get_session_principal(db: Session, *, token: str) -> Principal | None:
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == _token_hash(token))
    )
    if session is None or session.revoked_at is not None or ensure_utc(session.expires_at) <= now_utc():
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status != "ACTIVE":
        return None
    roles = user_roles(db, user.id, user.org_id)
    if not roles:
        return None
    return Principal(
        user_id=user.id,
        org_id=user.org_id,
        roles=frozenset(roles),
        session_id=session.id,
        source="session",
    )


def _find_user(db: Session, email: str, org_id: str | None) -> User | None:
    stmt = select(User).where(User.email == email)
    if org_id:
        stmt = stmt.where(User.org_id == org_id)
    users = list(db.scalars(stmt).all())
    if len(users) == 1:
        return users[0]
    return None


def assign_interviewer(
    db: Session,
    *,
    org_id: str,
    candidate_id: str,
    interviewer_id: str,
    scope: str = "CANDIDATE",
    expires_at: datetime | None = None,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> CandidateAssignment:
    assignment = CandidateAssignment(
        org_id=org_id,
        candidate_id=candidate_id,
        interviewer_id=interviewer_id,
        scope=scope,
        expires_at=expires_at,
    )
    db.add(assignment)
    audit = AuditService(db)
    audit.record(
        org_id=org_id,
        actor_id=actor_id or interviewer_id,
        action="CANDIDATE_ASSIGNED",
        resource_type="candidate",
        resource_id=candidate_id,
        after={"interviewer_id": interviewer_id, "scope": scope},
        trace_id=trace_id,
    )
    return assignment


def can_access_candidate(db: Session, principal: Principal, candidate: Candidate) -> bool:
    """资源级授权：组织内 HR/ADMIN 全量；面试官仅限分配记录。"""
    if candidate.org_id != principal.org_id:
        return False
    if principal.source == "header":
        # 请求头身份只在测试环境允许，测试模式无法表达具体用户分配关系。
        return True
    if principal.has_role("ADMIN") or principal.has_role("HR"):
        return True
    if principal.has_role("INTERVIEWER") and principal.user_id:
        now = now_utc()
        assignment = db.scalar(
            select(CandidateAssignment).where(
                CandidateAssignment.candidate_id == candidate.id,
                CandidateAssignment.interviewer_id == principal.user_id,
                CandidateAssignment.org_id == principal.org_id,
                CandidateAssignment.expires_at.is_(None)
                | (CandidateAssignment.expires_at > now),
            )
        )
        return assignment is not None
    return False


def candidate_scope_filter(db: Session, principal: Principal):
    """返回按权限裁剪候选人列表的查询条件（供列表端点复用）。"""
    if principal.has_role("HR"):
        return True
    if principal.has_role("INTERVIEWER") and principal.user_id:
        assigned_ids = select(CandidateAssignment.candidate_id).where(
            CandidateAssignment.org_id == principal.org_id,
            CandidateAssignment.interviewer_id == principal.user_id,
            CandidateAssignment.expires_at.is_(None)
            | (CandidateAssignment.expires_at > now_utc()),
        )
        return Candidate.id.in_(assigned_ids)
    return False
