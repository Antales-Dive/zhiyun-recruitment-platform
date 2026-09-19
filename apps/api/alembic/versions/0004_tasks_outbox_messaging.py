"""TASK-003 迁移：任务、Outbox、Consumer Ledger、幂等与流事件表。

- 扩展 tasks：aggregate_type/aggregate_id/current_attempt/next_retry_at/
  last_error_code（兼容式新增，旧列保留）；
- 新增 task_attempts / outbox_events / consumer_deliveries /
  idempotency_records / stream_events。

Revision ID: 0004_tasks_outbox_messaging
Revises: 0003_identity_and_audit
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tasks_outbox_messaging"
down_revision: str | None = "0003_identity_and_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        # 遗留 candidate_id 由 aggregate_id 取代（TARGET 表不再含该列）
        batch_op.alter_column(
            "candidate_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch_op.add_column(sa.Column("aggregate_type", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("aggregate_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_tasks_aggregate_id", ["aggregate_id"])
        batch_op.add_column(sa.Column("current_attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_tasks_next_retry_at", ["next_retry_at"])
        batch_op.add_column(sa.Column("last_error_code", sa.String(length=50), nullable=True))

    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempts_task_no"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=50), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_available_at", "outbox_events", ["available_at"])
    op.create_index("ix_outbox_events_org_id", "outbox_events", ["org_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_table(
        "consumer_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("consumer_name", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_consumer_deliveries_event"),
    )
    op.create_index("ix_consumer_deliveries_event_id", "consumer_deliveries", ["event_id"])
    op.create_index("ix_consumer_deliveries_status", "consumer_deliveries", ["status"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "actor_id", "operation", "key", name="uq_idempotency_scope_key"),
    )
    op.create_index("ix_idempotency_records_actor_id", "idempotency_records", ["actor_id"])
    op.create_index("ix_idempotency_records_org_id", "idempotency_records", ["org_id"])
    op.create_table(
        "stream_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("stream_type", sa.String(length=30), nullable=False),
        sa.Column("stream_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sequence"),
    )
    op.create_index("ix_stream_events_org_id", "stream_events", ["org_id"])
    op.create_index("ix_stream_events_stream_id", "stream_events", ["stream_id"])


def downgrade() -> None:
    op.drop_index("ix_stream_events_stream_id", table_name="stream_events")
    op.drop_index("ix_stream_events_org_id", table_name="stream_events")
    op.drop_table("stream_events")
    op.drop_index("ix_idempotency_records_org_id", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_actor_id", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_consumer_deliveries_status", table_name="consumer_deliveries")
    op.drop_index("ix_consumer_deliveries_event_id", table_name="consumer_deliveries")
    op.drop_table("consumer_deliveries")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_org_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_available_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_task_attempts_task_id", table_name="task_attempts")
    op.drop_table("task_attempts")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("last_error_code")
        batch_op.drop_index("ix_tasks_next_retry_at")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("current_attempt")
        batch_op.drop_index("ix_tasks_aggregate_id")
        batch_op.drop_column("aggregate_id")
        batch_op.drop_column("aggregate_type")
