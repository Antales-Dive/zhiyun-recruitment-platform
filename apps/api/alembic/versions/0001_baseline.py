"""基线迁移：与现有 SQLAlchemy 模型一致的初始 schema 快照。

本迁移由 TASK-001 人工核对生成，忠实反映原有
`Base.metadata.create_all` 的表结构，使 Alembic 能够接管既有数据库。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skills_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidates_job_id", "candidates", ["job_id"])
    op.create_index("ix_candidates_status", "candidates", ["status"])
    op.create_table(
        "candidate_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_candidate_files_sha256"),
    )
    op.create_index("ix_candidate_files_candidate_id", "candidate_files", ["candidate_id"])
    op.create_index("ix_candidate_files_sha256", "candidate_files", ["sha256"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_candidate_id", "tasks", ["candidate_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_table(
        "match_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("skill_score", sa.Integer(), nullable=False),
        sa.Column("experience_score", sa.Integer(), nullable=False),
        sa.Column("completeness_score", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("route", sa.String(length=30), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_results_candidate_id", "match_results", ["candidate_id"], unique=True)
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_documents_org_id", "knowledge_documents", ["org_id"])
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["status"])
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("allowed_roles_json", sa.Text(), nullable=False),
        sa.Column("parser_type", sa.String(length=30), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "content_sha256", name="uq_knowledge_version_content"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_knowledge_version_number"),
    )
    op.create_index("ix_knowledge_versions_content_sha256", "knowledge_versions", ["content_sha256"])
    op.create_index("ix_knowledge_versions_document_id", "knowledge_versions", ["document_id"])
    op.create_index("ix_knowledge_versions_status", "knowledge_versions", ["status"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
    )
    op.create_index("ix_knowledge_chunks_content_hash", "knowledge_chunks", ["content_hash"])
    op.create_index("ix_knowledge_chunks_version_id", "knowledge_chunks", ["version_id"])
    op.create_table(
        "knowledge_ingestion_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_ingestion_tasks_status", "knowledge_ingestion_tasks", ["status"])
    op.create_index("ix_knowledge_ingestion_tasks_version_id", "knowledge_ingestion_tasks", ["version_id"])
    op.create_table(
        "assistant_query_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=30), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("reliable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_query_logs_actor_role", "assistant_query_logs", ["actor_role"])
    op.create_index("ix_assistant_query_logs_org_id", "assistant_query_logs", ["org_id"])
    op.create_table(
        "assistant_citations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"]),
        sa.ForeignKeyConstraint(["query_id"], ["assistant_query_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_id", "chunk_id", name="uq_assistant_query_chunk"),
    )
    op.create_index("ix_assistant_citations_chunk_id", "assistant_citations", ["chunk_id"])
    op.create_index("ix_assistant_citations_query_id", "assistant_citations", ["query_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_citations_query_id", table_name="assistant_citations")
    op.drop_index("ix_assistant_citations_chunk_id", table_name="assistant_citations")
    op.drop_table("assistant_citations")
    op.drop_index("ix_assistant_query_logs_org_id", table_name="assistant_query_logs")
    op.drop_index("ix_assistant_query_logs_actor_role", table_name="assistant_query_logs")
    op.drop_table("assistant_query_logs")
    op.drop_index("ix_knowledge_ingestion_tasks_version_id", table_name="knowledge_ingestion_tasks")
    op.drop_index("ix_knowledge_ingestion_tasks_status", table_name="knowledge_ingestion_tasks")
    op.drop_table("knowledge_ingestion_tasks")
    op.drop_index("ix_knowledge_chunks_version_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_content_hash", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_versions_status", table_name="knowledge_versions")
    op.drop_index("ix_knowledge_versions_document_id", table_name="knowledge_versions")
    op.drop_index("ix_knowledge_versions_content_sha256", table_name="knowledge_versions")
    op.drop_table("knowledge_versions")
    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_org_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_match_results_candidate_id", table_name="match_results")
    op.drop_table("match_results")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_candidate_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_candidate_files_sha256", table_name="candidate_files")
    op.drop_index("ix_candidate_files_candidate_id", table_name="candidate_files")
    op.drop_table("candidate_files")
    op.drop_index("ix_candidates_status", table_name="candidates")
    op.drop_index("ix_candidates_job_id", table_name="candidates")
    op.drop_table("candidates")
    op.drop_table("jobs")
