"""TASK-004 迁移：简历版本/块/结构化档案表与组织级去重。

- 新增 resume_versions / resume_blocks / parsed_profiles；
- candidate_files：全局 SHA-256 去重改为组织级
  (org_id, candidate_id, sha256)，消除跨组织存在性泄露；
- candidate_files 新增 storage_key（存储键与原始文件名分离）。

Revision ID: 0005_resume_parsing
Revises: 0004_tasks_outbox_messaging
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_resume_parsing"
down_revision: str | None = "0004_tasks_outbox_messaging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_files") as batch_op:
        batch_op.add_column(sa.Column("storage_key", sa.String(length=255), nullable=True))
        batch_op.drop_constraint("uq_candidate_files_sha256", type_="unique")
        batch_op.create_unique_constraint(
            "uq_candidate_files_org_sha256", ["org_id", "candidate_id", "sha256"]
        )

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("quality_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["candidate_files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "version_no", name="uq_resume_versions_candidate_no"),
    )
    op.create_index("ix_resume_versions_candidate_id", "resume_versions", ["candidate_id"])
    op.create_index("ix_resume_versions_file_id", "resume_versions", ["file_id"])
    op.create_table(
        "resume_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resume_version_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=50), nullable=True),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_version_id"], ["resume_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_version_id", "ordinal", name="uq_resume_blocks_version_ordinal"),
    )
    op.create_index("ix_resume_blocks_resume_version_id", "resume_blocks", ["resume_version_id"])
    op.create_table(
        "parsed_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resume_version_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_version_id"], ["resume_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_version_id", name="uq_parsed_profiles_version"),
    )
    op.create_index("ix_parsed_profiles_resume_version_id", "parsed_profiles", ["resume_version_id"])


def downgrade() -> None:
    op.drop_index("ix_parsed_profiles_resume_version_id", table_name="parsed_profiles")
    op.drop_table("parsed_profiles")
    op.drop_index("ix_resume_blocks_resume_version_id", table_name="resume_blocks")
    op.drop_table("resume_blocks")
    op.drop_index("ix_resume_versions_file_id", table_name="resume_versions")
    op.drop_index("ix_resume_versions_candidate_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    with op.batch_alter_table("candidate_files") as batch_op:
        batch_op.drop_constraint("uq_candidate_files_org_sha256", type_="unique")
        batch_op.create_unique_constraint("uq_candidate_files_sha256", ["sha256"])
        batch_op.drop_column("storage_key")
