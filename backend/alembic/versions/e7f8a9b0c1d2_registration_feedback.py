"""registration approval and feedback

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-10-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("approval_status", sa.String(length=16), nullable=False, server_default="approved"),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_user_id"), "feedback", ["user_id"], unique=False)
    op.create_index(op.f("ix_feedback_status"), "feedback", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_status"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_user_id"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_column("users", "approval_status")
