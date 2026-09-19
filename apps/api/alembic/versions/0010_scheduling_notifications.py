"""TASK-009 迁移：排期、预约与通知账本。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_scheduling_notifications"
down_revision: str | None = "0009_questionnaires"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_schedule_slots_interval"),
    )
    op.create_index("ix_schedule_slots_org_id", "schedule_slots", ["org_id"])
    op.create_index("ix_schedule_slots_resource", "schedule_slots", ["resource_id"])
    op.create_table(
        "schedule_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_schedule_invitations_token"),
    )
    op.create_index("ix_schedule_invitations_candidate_id", "schedule_invitations", ["candidate_id"])
    op.create_table(
        "reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slot_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider_event_id", sa.String(length=100), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("active_slot_key", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["slot_id"], ["schedule_slots.id"]),
        sa.PrimaryKeyConstraint("id"),
        # 活动预约每时段唯一：ACTIVE 时 active_slot_key=slot_id，取消后置 NULL
        sa.UniqueConstraint("active_slot_key", name="uq_reservations_active_slot"),
    )
    op.create_index("ix_reservations_slot_id", "reservations", ["slot_id"])
    op.create_index("ix_reservations_candidate_id", "reservations", ["candidate_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("template_version_id", sa.String(length=50), nullable=False),
        sa.Column("recipient_ref", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "channel", "idempotency_key", name="uq_notifications_idem"),
    )
    op.create_index("ix_notifications_org_id", "notifications", ["org_id"])
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "attempt_no", name="uq_notification_deliveries_attempt"),
    )
    op.create_index("ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_notification_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notifications_org_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_reservations_candidate_id", table_name="reservations")
    op.drop_index("ix_reservations_slot_id", table_name="reservations")
    op.drop_table("reservations")
    op.drop_index("ix_schedule_invitations_candidate_id", table_name="schedule_invitations")
    op.drop_table("schedule_invitations")
    op.drop_index("ix_schedule_slots_resource", table_name="schedule_slots")
    op.drop_index("ix_schedule_slots_org_id", table_name="schedule_slots")
    op.drop_table("schedule_slots")
