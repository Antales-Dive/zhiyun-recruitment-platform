"""为模型调用记录补充组织归属，避免指标跨组织泄露。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_model_run_org"
down_revision: str | None = "0011_interviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_runs", sa.Column("org_id", sa.String(length=64), nullable=True))
    op.create_index("ix_model_runs_org_id", "model_runs", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_model_runs_org_id", table_name="model_runs")
    op.drop_column("model_runs", "org_id")
