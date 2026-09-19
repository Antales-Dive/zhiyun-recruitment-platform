"""TASK-002 审计集成测试：敏感动作审计、脱敏与授权查询。"""
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
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine

TEST_PASSWORD = f"pw-{uuid4().hex[:16]}"


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


def make_user(db_session, org_id, email, role="HR"):
    user = create_user(db_session, org_id=org_id, email=email, password=TEST_PASSWORD)
    assign_role(db_session, user_id=user.id, org_id=org_id, role_code=role)
    return user


def audit_rows(db_session, action=None):
    from app.infrastructure.models import AuditLog
    from sqlalchemy import select

    stmt = select(AuditLog).order_by(AuditLog.created_at)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return list(db_session.scalars(stmt).all())


def login_token(db_session, email):
    result = login(db_session, email=email, password=TEST_PASSWORD)
    db_session.commit()
    return result.token


class TestAuditRecording:
    def test_login_success_is_audited(self, org_a, db_session):
        user = make_user(db_session, org_a.id, "hr-audit@org-a.example")
        db_session.commit()

        response = request(
            "POST", "/api/v1/auth/login", json={"email": "hr-audit@org-a.example", "password": TEST_PASSWORD}
        )

        assert response.status_code == 200
        rows = [row for row in audit_rows(db_session, "AUTH_LOGIN") if row.actor_id == user.id]
        assert len(rows) == 1
        assert rows[0].org_id == "org-a"

    def test_failed_login_is_audited_as_security_event(self, org_a, db_session):
        user = make_user(db_session, org_a.id, "fail-audit@org-a.example")
        db_session.commit()

        request(
            "POST", "/api/v1/auth/login", json={"email": "fail-audit@org-a.example", "password": "bad-pass"}
        )

        rows = [
            row for row in audit_rows(db_session, "AUTH_LOGIN_FAILED") if row.resource_id == user.id
        ]
        assert len(rows) == 1
        assert rows[0].reason

    def test_logout_is_audited(self, org_a, db_session):
        user = make_user(db_session, org_a.id, "out-audit@org-a.example")
        db_session.commit()
        token = login_token(db_session, "out-audit@org-a.example")

        request("POST", "/api/v1/auth/logout", token=token)

        rows = [row for row in audit_rows(db_session, "AUTH_LOGOUT") if row.actor_id == user.id]
        assert len(rows) == 1

    def test_role_assignment_is_audited(self, org_a, db_session):
        user = make_user(db_session, org_a.id, "role-audit@org-a.example", role="HR")
        db_session.commit()

        assign_role(db_session, user_id=user.id, org_id=org_a.id, role_code="INTERVIEWER")
        db_session.commit()

        rows = [
            row
            for row in audit_rows(db_session, "ROLE_ASSIGNED")
            if row.resource_id == user.id
        ]
        assert any("INTERVIEWER" in row.after_json for row in rows)

    def test_audit_records_never_contain_credentials_or_tokens(self, org_a, db_session):
        make_user(db_session, org_a.id, "leak-audit@org-a.example")
        db_session.commit()
        token = login_token(db_session, "leak-audit@org-a.example")
        request("POST", "/api/v1/auth/logout", token=token)

        db_session.expire_all()
        for row in audit_rows(db_session):
            serialized = str(row.__dict__)
            assert TEST_PASSWORD not in serialized
            assert token not in serialized
            assert "leak-audit@org-a.example" not in str(row.before_json or "") + str(row.after_json or "")


class TestAuditQuery:
    def test_audit_list_requires_auditor_or_admin(self, org_a, db_session):
        make_user(db_session, org_a.id, "hr-query@org-a.example", role="HR")
        make_user(db_session, org_a.id, "audit-query@org-a.example", role="AUDITOR")
        db_session.commit()
        hr_token = login_token(db_session, "hr-query@org-a.example")
        auditor_token = login_token(db_session, "audit-query@org-a.example")

        forbidden = request("GET", "/api/v1/audit", token=hr_token)
        assert forbidden.status_code == 403

        allowed = request("GET", "/api/v1/audit", token=auditor_token)
        assert allowed.status_code == 200
        assert isinstance(allowed.json()["data"]["items"], list)

    def test_audit_list_is_org_scoped(self, org_a, org_b, db_session):
        make_user(db_session, org_a.id, "scoped-audit@org-a.example", role="AUDITOR")
        make_user(db_session, org_b.id, "hr-b@org-b.example", role="HR")
        db_session.commit()
        auditor_token = login_token(db_session, "scoped-audit@org-a.example")

        response = request("GET", "/api/v1/audit", token=auditor_token)

        assert response.status_code == 200
        org_ids = {item["org_id"] for item in response.json()["data"]["items"]}
        assert org_ids == {"org-a"}

    def test_audit_list_supports_cursor_pagination(self, org_a, db_session):
        make_user(db_session, org_a.id, "page-audit@org-a.example", role="AUDITOR")
        make_user(db_session, org_a.id, "page-hr@org-a.example", role="HR")
        db_session.commit()
        token = login_token(db_session, "page-audit@org-a.example")
        for _ in range(3):
            request(
                "POST",
                "/api/v1/auth/login",
                json={"email": "page-hr@org-a.example", "password": "bad-pass"},
            )

        first = request("GET", "/api/v1/audit?limit=2", token=token)
        body = first.json()["data"]
        assert len(body["items"]) == 2
        assert body["next_cursor"]

        second = request("GET", f"/api/v1/audit?limit=2&cursor={body['next_cursor']}", token=token)
        second_body = second.json()["data"]
        assert len(second_body["items"]) >= 1
        first_ids = {item["id"] for item in body["items"]}
        second_ids = {item["id"] for item in second_body["items"]}
        assert first_ids.isdisjoint(second_ids)
