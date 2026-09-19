"""展开式迁移：为常规表补齐 updated_at 基础字段。

按 04-contracts-and-data.md §7 的基线约定“常规表均含 created_at、
updated_at”，以兼容方式（只新增列、不删除旧列）为缺失该字段的
招聘域常规表补列，并回填现有数据。

使用 batch_alter_table：SQLite 不允许 ADD COLUMN 携带非恒定默认值，
批量重建表可同时完成回填；MySQL 上等价于普通 ALTER。

Revision ID: 0002_expand_base_columns
Revises: 0001_baseline
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_expand_base_columns"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE_COLUMNS = ("jobs", "candidates", "candidate_files", "match_results")


def upgrade() -> None:
    for table in _BASE_COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                )
            )


def downgrade() -> None:
    for table in reversed(_BASE_COLUMNS):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("updated_at")
