"""strategy sharing and versions

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-22 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_strategies", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("user_strategies", sa.Column("source_strategy_id", sa.Integer(), nullable=True))
    op.add_column("user_strategies", sa.Column("source_version", sa.Integer(), nullable=True))
    op.add_column("user_strategies", sa.Column("forked_at", sa.DateTime(), nullable=True))
    # SQLite 不支持 ALTER 添加外键；应用删除源策略时会手动把副本 source 置空。
    op.create_index("ix_user_strategies_source_strategy_id", "user_strategies", ["source_strategy_id"])

    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["user_strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
    )
    op.create_index(
        op.f("ix_strategy_versions_strategy_id"), "strategy_versions", ["strategy_id"], unique=False
    )

    op.create_table(
        "strategy_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["user_strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_strategy_shares_strategy_id"), "strategy_shares", ["strategy_id"], unique=False)
    op.create_index(op.f("ix_strategy_shares_owner_id"), "strategy_shares", ["owner_id"], unique=False)
    op.create_index(
        op.f("ix_strategy_shares_target_user_id"), "strategy_shares", ["target_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_strategy_shares_target_user_id"), table_name="strategy_shares")
    op.drop_index(op.f("ix_strategy_shares_owner_id"), table_name="strategy_shares")
    op.drop_index(op.f("ix_strategy_shares_strategy_id"), table_name="strategy_shares")
    op.drop_table("strategy_shares")
    op.drop_index(op.f("ix_strategy_versions_strategy_id"), table_name="strategy_versions")
    op.drop_table("strategy_versions")
    op.drop_index("ix_user_strategies_source_strategy_id", table_name="user_strategies")
    op.drop_column("user_strategies", "forked_at")
    op.drop_column("user_strategies", "source_version")
    op.drop_column("user_strategies", "source_strategy_id")
    op.drop_column("user_strategies", "version")
