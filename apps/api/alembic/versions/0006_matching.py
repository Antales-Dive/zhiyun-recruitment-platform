"""TASK-005 迁移：岗位版本、评分规则、分析运行与证据表。

- 新增 job_versions / rubric_versions / analysis_runs / agent_runs /
  routing_decisions / model_runs / prompt_versions；
- jobs 增加 active_version_id（当前活动岗位版本）；
- candidates 增加 row_version（乐观锁）。

Revision ID: 0006_matching
Revises: 0005_resume_parsing
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_matching"
down_revision: str | None = "0005_resume_parsing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("active_version_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.add_column(sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))

    op.create_table(
        "job_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requirements_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "version_no", name="uq_job_versions_job_no"),
    )
    op.create_index("ix_job_versions_job_id", "job_versions", ["job_id"])
    op.create_table(
        "rubric_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_version_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("dimensions_json", sa.Text(), nullable=False),
        sa.Column("thresholds_json", sa.Text(), nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_version_id"], ["job_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_version_id", "version_no", name="uq_rubric_versions_job_no"),
    )
    op.create_index("ix_rubric_versions_job_version_id", "rubric_versions", ["job_version_id"])
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("job_version_id", sa.String(length=36), nullable=False),
        sa.Column("resume_version_id", sa.String(length=36), nullable=False),
        sa.Column("rubric_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("route", sa.String(length=30), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["job_version_id"], ["job_versions.id"]),
        sa.ForeignKeyConstraint(["resume_version_id"], ["resume_versions.id"]),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_runs_candidate_id", "analysis_runs", ["candidate_id"])
    op.create_index("ix_analysis_runs_org_id", "analysis_runs", ["org_id"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("model_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id", "agent_type", name="uq_agent_runs_run_agent"),
    )
    op.create_index("ix_agent_runs_analysis_run_id", "agent_runs", ["analysis_run_id"])
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routing_decisions_candidate_id", "routing_decisions", ["candidate_id"])
    op.create_table(
        "model_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("trace_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_runs_purpose", "model_runs", ["purpose"])
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("template_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purpose", "version_no", name="uq_prompt_versions_purpose_no"),
    )
    op.create_index("ix_prompt_versions_purpose", "prompt_versions", ["purpose"])


def downgrade() -> None:
    op.drop_index("ix_prompt_versions_purpose", table_name="prompt_versions")
    op.drop_table("prompt_versions")
    op.drop_index("ix_model_runs_purpose", table_name="model_runs")
    op.drop_table("model_runs")
    op.drop_index("ix_routing_decisions_candidate_id", table_name="routing_decisions")
    op.drop_table("routing_decisions")
    op.drop_index("ix_agent_runs_analysis_run_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_analysis_runs_org_id", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_candidate_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index("ix_rubric_versions_job_version_id", table_name="rubric_versions")
    op.drop_table("rubric_versions")
    op.drop_index("ix_job_versions_job_id", table_name="job_versions")
    op.drop_table("job_versions")
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.drop_column("row_version")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("active_version_id")
