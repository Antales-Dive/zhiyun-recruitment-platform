"""TASK-007 迁移：检索运行记录与查询日志扩展列。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_retrieval_runs"
down_revision: str | None = "0007_knowledge_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("filter_json", sa.Text(), nullable=False),
        sa.Column("dense_ids_json", sa.Text(), nullable=False),
        sa.Column("sparse_ids_json", sa.Text(), nullable=False),
        sa.Column("merged_ids_json", sa.Text(), nullable=False),
        sa.Column("reranked_ids_json", sa.Text(), nullable=False),
        sa.Column("config_version", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["query_id"], ["assistant_query_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_runs_query_id", "retrieval_runs", ["query_id"])
    with op.batch_alter_table("assistant_query_logs") as batch_op:
        batch_op.add_column(sa.Column("retrieval_run_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("model_run_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_query_logs") as batch_op:
        batch_op.drop_column("model_run_id")
        batch_op.drop_column("retrieval_run_id")
    op.drop_index("ix_retrieval_runs_query_id", table_name="retrieval_runs")
    op.drop_table("retrieval_runs")
