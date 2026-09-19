"""TASK-002 授权集成测试：会话认证、RBAC、组织隔离与资源级策略。

本模块显式关闭请求头模拟身份模式（会话认证），验证真实登录链路、
跨组织隔离、面试官分配限制与审计员只读策略。
"""
import asyncio
from uuid import uuid4

import httpx
import pytest
from app.config import settings
from app.domains.identity.service import (
    assign_role,
    create_organization,
    create_user,
    ensure_roles,
    get_or_create_default_org,
    login,
    rate_limiter,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import User
from sqlalchemy import select

# 测试专用随机口令：每次进程生成一次，避免任何“固定凭据”入库
TEST_PASSWORD = f"pw-{uuid4().hex[:16]}"


def get_or_create_user(db_session, *, org_id, email, role):
    user = db_session.scalar(select(User).where(User.org_id == org_id, User.email == email))
    if user is None:
        user = create_user(db_session, org_id=org_id, email=email, password=TEST_PASSWORD)
        assign_role(db_session, user_id=user.id, org_id=org_id, role_code=role)
        db_session.commit()
    return user


@pytest.fixture(autouse=True)
def clear_rate_limits():
    rate_limiter.reset_all()
    yield
    rate_limiter.reset_all()


@pytest.fixture(scope="module", autouse=True)
def session_auth_mode():
    Base.metadata.create_all(bind=engine)
    settings.allow_header_auth = False
    db = SessionLocal()
    ensure_roles(db)
    get_or_create_default_org(db)
    db.commit()
    db.close()
    yield
    settings.allow_header_auth = True


@pytest.fixture()
def db_session():
    db = SessionLocal()
    yield db
    db.close()


def create_app():
    from app.main import app

    return app


def request(method, url, token=None, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def run():
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, url, headers=headers, **kwargs)

    return asyncio.run(run())


@pytest.fixture()
def org_a(db_session):
    return create_organization(db_session, org_id="org-a", name="甲组织")


@pytest.fixture()
def org_b(db_session):
    return create_organization(db_session, org_id="org-b", name="乙组织")


@pytest.fixture()
def hr_user(org_a, db_session):
    return get_or_create_user(db_session, org_id=org_a.id, email="hr@org-a.example", role="HR")


@pytest.fixture()
def interviewer_user(org_a, db_session):
    return get_or_create_user(db_session, org_id=org_a.id, email="iv@org-a.example", role="INTERVIEWER")


@pytest.fixture()
def auditor_user(org_a, db_session):
    return get_or_create_user(db_session, org_id=org_a.id, email="audit@org-a.example", role="AUDITOR")


def login_token(db_session, email):
    result = login(db_session, email=email, password=TEST_PASSWORD)
    db_session.commit()
    return result.token


def create_job(db_session, *, org_id, title):
    from app.infrastructure.models import Job

    job = Job(org_id=org_id, title=title)
    db_session.add(job)
    db_session.commit()
    return job


class TestSessionAuthentication:
    def test_role_headers_are_ignored_without_session(self):
        response = request(
            "POST",
            "/api/v1/jobs",
            json={"title": "越权岗位"},
            headers={"X-User-Role": "ADMIN", "X-Org-Id": "default"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"

    def test_login_with_wrong_password_fails(self, hr_user):
        response = request(
            "POST", "/api/v1/auth/login", json={"email": "hr@org-a.example", "password": "wrong-pass"}
        )

        assert response.status_code == 401

    def test_login_success_returns_token_and_roles(self, hr_user):
        response = request(
            "POST",
            "/api/v1/auth/login",
            json={"email": "hr@org-a.example", "password": TEST_PASSWORD},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["token"]
        assert data["org_id"] == "org-a"
        assert "HR" in data["roles"]

    def test_repeated_failures_trigger_rate_limit(self, hr_user):
        for _ in range(settings.login_rate_limit_max):
            request(
                "POST",
                "/api/v1/auth/login",
                json={"email": "hr@org-a.example", "password": "bad-pass"},
            )

        response = request(
            "POST",
            "/api/v1/auth/login",
            json={"email": "hr@org-a.example", "password": "bad-pass"},
        )

        assert response.status_code == 429

    def test_me_returns_current_identity(self, hr_user, db_session):
        token = login_token(db_session, "hr@org-a.example")

        response = request("GET", "/api/v1/auth/me", token=token)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["org_id"] == "org-a"
        assert data["roles"] == ["HR"]

    def test_logout_revokes_session(self, hr_user, db_session):
        token = login_token(db_session, "hr@org-a.example")

        logout_response = request("POST", "/api/v1/auth/logout", token=token)
        assert logout_response.status_code == 200

        me_response = request("GET", "/api/v1/auth/me", token=token)
        assert me_response.status_code == 401


class TestOrgIsolation:
    def test_cross_org_job_is_not_listed(self, org_a, org_b, hr_user, db_session):
        token = login_token(db_session, "hr@org-a.example")
        other_org_job = create_job(db_session, org_id="org-b", title="乙组织机密岗位")

        response = request("GET", "/api/v1/jobs", token=token)

        assert response.status_code == 200
        titles = [item["title"] for item in response.json()["data"]]
        assert other_org_job.title not in titles

    def test_cross_org_job_cannot_be_used_for_import(self, org_b, hr_user, db_session):
        token = login_token(db_session, "hr@org-a.example")
        other_org_job = create_job(db_session, org_id="org-b", title="乙组织岗位")

        response = request(
            "POST",
            "/api/v1/candidates/import",
            data={"job_id": other_org_job.id},
            files={"files": ("r.txt", "张三\nPython".encode(), "text/plain")},
            token=token,
        )

        assert response.status_code == 404


class TestResourceLevelPolicy:
    def test_interviewer_cannot_read_unassigned_candidate_detail(self, org_a, interviewer_user, db_session):
        from app.infrastructure.models import Candidate, Job

        job = Job(org_id=org_a.id, title="权限岗位")
        db_session.add(job)
        db_session.flush()
        candidate = Candidate(org_id=org_a.id, job_id=job.id, name="未分配详情")
        db_session.add(candidate)
        db_session.commit()

        token = login_token(db_session, "iv@org-a.example")
        response = request("GET", f"/api/v1/candidates/{candidate.id}", token=token)

        assert response.status_code == 403
        db_session.delete(candidate)
        db_session.delete(job)
        db_session.commit()

    def test_interviewer_only_sees_assigned_candidates(self, org_a, hr_user, interviewer_user, db_session):
        from app.domains.identity.service import assign_interviewer
        from app.infrastructure.models import Candidate, Job

        job = Job(org_id=org_a.id, title="算法工程师")
        db_session.add(job)
        db_session.flush()
        assigned = Candidate(job_id=job.id, org_id=org_a.id, name="被分配候选人")
        hidden = Candidate(job_id=job.id, org_id=org_a.id, name="未分配候选人")
        db_session.add_all([assigned, hidden])
        db_session.flush()
        assign_interviewer(
            db_session,
            org_id=org_a.id,
            candidate_id=assigned.id,
            interviewer_id=interviewer_user.id,
        )
        db_session.commit()

        hr_token = login_token(db_session, "hr@org-a.example")
        iv_token = login_token(db_session, "iv@org-a.example")

        hr_response = request("GET", "/api/v1/candidates", token=hr_token)
        assert len(hr_response.json()["data"]) == 2

        iv_response = request("GET", "/api/v1/candidates", token=iv_token)
        names = [item["name"] for item in iv_response.json()["data"]]
        assert names == ["被分配候选人"]

    def test_auditor_cannot_create_job(self, auditor_user, db_session):
        token = login_token(db_session, "audit@org-a.example")

        response = request("POST", "/api/v1/jobs", json={"title": "审计员尝试建岗"}, token=token)

        assert response.status_code == 403

    def test_auditor_cannot_list_candidates(self, auditor_user, db_session):
        token = login_token(db_session, "audit@org-a.example")

        response = request("GET", "/api/v1/candidates", token=token)

        assert response.status_code == 403
