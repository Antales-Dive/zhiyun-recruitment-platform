"""TASK-006 迁移：知识 ACL、索引清单与摄取步骤追踪。

- 新增 knowledge_acl（结构化授权，取代角色 JSON）；
- 新增 knowledge_index_manifests（Dense/Sparse 数量一致后才可发布）；
- knowledge_documents 增加 active_version_id；
- knowledge_chunks 增加 parent_id/chunk_type/token_count（Parent-Child 元数据）；
- knowledge_versions 增加 embedding_model_version。

Revision ID: 0007_knowledge_lifecycle
Revises: 0006_matching
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_knowledge_lifecycle"
down_revision: str | None = "0006_matching"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.add_column(sa.Column("active_version_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("knowledge_versions") as batch_op:
        batch_op.add_column(sa.Column("embedding_model_version", sa.String(length=50), nullable=True))
    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("chunk_type", sa.String(length=20), nullable=False, server_default="child"))
        batch_op.add_column(sa.Column("token_count", sa.Integer(), nullable=True))

    op.create_table(
        "knowledge_acl",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_value", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "subject_type", "subject_value", name="uq_knowledge_acl_subject"),
    )
    op.create_index("ix_knowledge_acl_version_id", "knowledge_acl", ["version_id"])
    op.create_table(
        "knowledge_index_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("dense_count", sa.Integer(), nullable=False),
        sa.Column("sparse_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_index_manifests_version_id", "knowledge_index_manifests", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_index_manifests_version_id", table_name="knowledge_index_manifests")
    op.drop_table("knowledge_index_manifests")
    op.drop_index("ix_knowledge_acl_version_id", table_name="knowledge_acl")
    op.drop_table("knowledge_acl")
    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.drop_column("token_count")
        batch_op.drop_column("chunk_type")
        batch_op.drop_column("parent_id")
    with op.batch_alter_table("knowledge_versions") as batch_op:
        batch_op.drop_column("embedding_model_version")
    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.drop_column("row_version")
        batch_op.drop_column("active_version_id")
