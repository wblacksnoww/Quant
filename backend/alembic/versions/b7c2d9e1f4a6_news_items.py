"""news items for policy/news factors

Revision ID: b7c2d9e1f4a6
Revises: 9b1f4c2d7e30
Create Date: 2026-10-21 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c2d9e1f4a6"
down_revision: str | None = "9b1f4c2d7e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=600), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("sentiment", sa.Float(), nullable=False),
        sa.Column("policy_risk", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_news_items_published_at"), "news_items", ["published_at"], unique=False)
    op.create_index(op.f("ix_news_items_effective_at"), "news_items", ["effective_at"], unique=False)
    op.create_index(op.f("ix_news_items_raw_hash"), "news_items", ["raw_hash"], unique=True)
    op.create_index(op.f("ix_news_items_created_by"), "news_items", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_news_items_created_by"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_raw_hash"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_effective_at"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_published_at"), table_name="news_items")
    op.drop_table("news_items")
