"""distributed sessions and durable backtest jobs

Revision ID: 9b1f4c2d7e30
Revises: 2e6eb884e37d
Create Date: 2026-10-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b1f4c2d7e30"
down_revision: str | None = "2e6eb884e37d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_leases",
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("state", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_session_leases_user_id"), "session_leases", ["user_id"], unique=False)
    op.create_index(op.f("ix_session_leases_run_id"), "session_leases", ["run_id"], unique=False)
    op.create_index(op.f("ix_session_leases_owner"), "session_leases", ["owner"], unique=False)
    op.create_index(op.f("ix_session_leases_expires_at"), "session_leases", ["expires_at"], unique=False)

    op.create_table(
        "session_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_commands_key"), "session_commands", ["key"], unique=False)

    op.create_table(
        "backtest_jobs",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(op.f("ix_backtest_jobs_user_id"), "backtest_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_backtest_jobs_status"), "backtest_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_backtest_jobs_owner"), "backtest_jobs", ["owner"], unique=False)
    op.create_index(op.f("ix_backtest_jobs_created_at"), "backtest_jobs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_backtest_jobs_created_at"), table_name="backtest_jobs")
    op.drop_index(op.f("ix_backtest_jobs_owner"), table_name="backtest_jobs")
    op.drop_index(op.f("ix_backtest_jobs_status"), table_name="backtest_jobs")
    op.drop_index(op.f("ix_backtest_jobs_user_id"), table_name="backtest_jobs")
    op.drop_table("backtest_jobs")

    op.drop_index(op.f("ix_session_commands_key"), table_name="session_commands")
    op.drop_table("session_commands")

    op.drop_index(op.f("ix_session_leases_expires_at"), table_name="session_leases")
    op.drop_index(op.f("ix_session_leases_owner"), table_name="session_leases")
    op.drop_index(op.f("ix_session_leases_run_id"), table_name="session_leases")
    op.drop_index(op.f("ix_session_leases_user_id"), table_name="session_leases")
    op.drop_table("session_leases")
