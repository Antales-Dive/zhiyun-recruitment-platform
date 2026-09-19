"""TASK-002 迁移：身份与审计表、默认组织/角色，并为招聘表回填 org_id。

- 新增 organizations/users/roles/role_bindings/sessions/audit_logs/
  candidate_assignments；
- 种子默认组织与四个固定角色（ADMIN/HR/INTERVIEWER/AUDITOR）；
- 为 jobs/candidates/candidate_files/tasks/match_results 以兼容方式
  增加 org_id 并回填 DEFAULT_ORG_ID（仅允许显式配置一个默认组织）。

Revision ID: 0003_identity_and_audit
Revises: 0002_expand_base_columns
Create Date: 2026-09-13
"""
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from app.config import settings

revision: str = "0003_identity_and_audit"
down_revision: str | None = "0002_expand_base_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG_TABLES = ("jobs", "candidates", "candidate_files", "tasks", "match_results")
_ROLE_CODES = ("ADMIN", "HR", "INTERVIEWER", "AUDITOR")
_NOW = datetime.now(UTC)


def _seed_roles() -> None:
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.String(36)),
        sa.column("code", sa.String(30)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        roles_table,
        [
            {"id": str(uuid4()), "code": code, "created_at": _NOW}
            for code in _ROLE_CODES
        ],
    )


def _seed_default_org() -> None:
    orgs_table = sa.table(
        "organizations",
        sa.column("id", sa.String(64)),
        sa.column("name", sa.String(200)),
        sa.column("status", sa.String(30)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        orgs_table,
        [
            {
                "id": settings.default_org_id,
                "name": "默认组织",
                "status": "ACTIVE",
                "created_at": _NOW,
                "updated_at": _NOW,
            }
        ],
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "email", name="uq_users_org_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
    )
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "org_id", "role_id", name="uq_role_bindings_user_org_role"),
    )
    op.create_index("ix_role_bindings_role_id", "role_bindings", ["role_id"])
    op.create_index("ix_role_bindings_user_id", "role_bindings", ["user_id"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("trace_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_table(
        "candidate_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("interviewer_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["interviewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidate_assignments_candidate_id", "candidate_assignments", ["candidate_id"])
    op.create_index("ix_candidate_assignments_interviewer_id", "candidate_assignments", ["interviewer_id"])
    op.create_index("ix_candidate_assignments_org_id", "candidate_assignments", ["org_id"])

    _seed_default_org()
    _seed_roles()

    # 招聘域常规表回填 org_id；默认值保留作为数据完整性兜底
    for table in _ORG_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "org_id",
                    sa.String(length=64),
                    nullable=False,
                    server_default=settings.default_org_id,
                )
            )
            batch_op.create_index(f"ix_{table}_org_id", ["org_id"])


def downgrade() -> None:
    for table in reversed(_ORG_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_org_id")
            batch_op.drop_column("org_id")
    op.drop_index("ix_candidate_assignments_org_id", table_name="candidate_assignments")
    op.drop_index("ix_candidate_assignments_interviewer_id", table_name="candidate_assignments")
    op.drop_index("ix_candidate_assignments_candidate_id", table_name="candidate_assignments")
    op.drop_table("candidate_assignments")
    op.drop_index("ix_audit_logs_org_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_role_bindings_user_id", table_name="role_bindings")
    op.drop_index("ix_role_bindings_role_id", table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_table("roles")
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
