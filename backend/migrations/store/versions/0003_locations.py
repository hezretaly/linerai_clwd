"""The lots of a group, and which one a car stands on and a visit is at.

A group is one database, so a manager works across all of its lots -- and a
dealership with more than one lot booked every visit at the single address in
`dealership`, telling a buyer who wanted a car at another store that it was
somewhere else and then booking them where it was not. `locations` holds each
lot; `vehicles.location_id` and `appointments.location_id` say which.

Both columns start empty. `app/locations.py` fills the table from the profile
and places the cars at the next boot; an appointment with no location is read
as the primary, which is where every visit before this was booked.

Revision ID: 0003_locations
Revises: 0002_one_part_per_version
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_locations"
down_revision = "0002_one_part_per_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("hours_json", sa.Text(), nullable=False),
        sa.Column("aliases_json", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("key", name=op.f("uq_locations_key")),
    )
    # Batch mode rebuilds the table on SQLite, which cannot add a foreign key
    # to one that exists; on Postgres it is a plain ALTER.
    for table in ("vehicles", "appointments"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("location_id", sa.String(length=36), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{table}_location_id"), ["location_id"], unique=False)
            batch_op.create_foreign_key(
                batch_op.f(f"fk_{table}_location_id_locations"), "locations", ["location_id"], ["id"]
            )


def downgrade() -> None:
    for table in ("appointments", "vehicles"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(batch_op.f(f"fk_{table}_location_id_locations"), type_="foreignkey")
            batch_op.drop_index(batch_op.f(f"ix_{table}_location_id"))
            batch_op.drop_column("location_id")
    op.drop_table("locations")
