"""Add `users.out`: a rep temporarily off the floor.

A rep going to lunch or taking a day off needs a way to say so, without
touching who they already own -- that is `active` (deactivate, hand
everything back). `out` is the narrower, reversible flag beside it.

Revision ID: 0003_rep_out_status
Revises: 0002_one_part_per_version
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_rep_out_status"
down_revision = "0002_one_part_per_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("out", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.alter_column("out", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("out")
