"""TASK-008 迁移：问卷版本、邀请与提交。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_questionnaires"
down_revision: str | None = "0008_retrieval_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "questionnaires",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_questionnaires_org_id", "questionnaires", ["org_id"])
    op.create_table(
        "questionnaire_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("questionnaire_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("questions_json", sa.Text(), nullable=False),
        sa.Column("scoring_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["questionnaire_id"], ["questionnaires.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("questionnaire_id", "version_no", name="uq_questionnaire_versions_no"),
    )
    op.create_index("ix_questionnaire_versions_questionnaire_id", "questionnaire_versions", ["questionnaire_id"])
    op.create_table(
        "questionnaire_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["questionnaire_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_questionnaire_invitations_token"),
    )
    op.create_index("ix_questionnaire_invitations_candidate_id", "questionnaire_invitations", ["candidate_id"])
    op.create_index("ix_questionnaire_invitations_version_id", "questionnaire_invitations", ["version_id"])
    op.create_table(
        "questionnaire_responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invitation_id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=64), nullable=False),
        sa.Column("answers_json", sa.Text(), nullable=False),
        sa.Column("score_json", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invitation_id"], ["questionnaire_invitations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id", "submission_id", name="uq_questionnaire_responses_submission"),
    )
    op.create_index("ix_questionnaire_responses_invitation_id", "questionnaire_responses", ["invitation_id"])


def downgrade() -> None:
    op.drop_index("ix_questionnaire_responses_invitation_id", table_name="questionnaire_responses")
    op.drop_table("questionnaire_responses")
    op.drop_index("ix_questionnaire_invitations_version_id", table_name="questionnaire_invitations")
    op.drop_index("ix_questionnaire_invitations_candidate_id", table_name="questionnaire_invitations")
    op.drop_table("questionnaire_invitations")
    op.drop_index("ix_questionnaire_versions_questionnaire_id", table_name="questionnaire_versions")
    op.drop_table("questionnaire_versions")
    op.drop_index("ix_questionnaires_org_id", table_name="questionnaires")
    op.drop_table("questionnaires")
