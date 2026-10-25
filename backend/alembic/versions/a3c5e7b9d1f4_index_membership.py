"""index membership ranges (point in time)

Revision ID: a3c5e7b9d1f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-26 02:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c5e7b9d1f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_membership",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("index_code", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("index_code", "symbol", "start_date", name="uq_index_member_entry"),
    )
    op.create_index(op.f("ix_index_membership_index_code"), "index_membership", ["index_code"], unique=False)
    op.create_index(op.f("ix_index_membership_symbol"), "index_membership", ["symbol"], unique=False)
    op.create_index(
        "ix_index_membership_lookup",
        "index_membership",
        ["index_code", "start_date", "end_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_index_membership_lookup", table_name="index_membership")
    op.drop_index(op.f("ix_index_membership_symbol"), table_name="index_membership")
    op.drop_index(op.f("ix_index_membership_index_code"), table_name="index_membership")
    op.drop_table("index_membership")
