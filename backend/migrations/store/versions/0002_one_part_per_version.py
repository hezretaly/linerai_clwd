"""One wording per assistant per settings version.

`assistant_parts` had nothing stopping two rows for the same part of the same
version, and with two the wording an assistant ran under was whichever row the
database returned last -- insertion order on SQLite, anything at all on
Postgres. Found by running the gate on Postgres, where the writing assistant
drafted under the product's own instructions while a dealership's sat in the
table beside them.

The newest row of any pair is kept, by when it was last written, then the
constraint makes a second one impossible.

Revision ID: 0002_one_part_per_version
Revises: 0001_store_baseline
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_one_part_per_version"
down_revision = "0001_store_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, settings_id, part FROM assistant_parts "
        "ORDER BY updated_at DESC, id DESC"
    )).fetchall()
    kept: set[tuple[str, str]] = set()
    extra = []
    for row in rows:
        key = (row.settings_id, row.part)
        if key in kept:
            extra.append({"id": row.id})
        else:
            kept.add(key)
    if extra:
        conn.execute(sa.text("DELETE FROM assistant_parts WHERE id = :id"), extra)
    with op.batch_alter_table("assistant_parts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_assistant_parts_settings_id", ["settings_id", "part"]
        )


def downgrade() -> None:
    with op.batch_alter_table("assistant_parts") as batch_op:
        batch_op.drop_constraint("uq_assistant_parts_settings_id", type_="unique")
