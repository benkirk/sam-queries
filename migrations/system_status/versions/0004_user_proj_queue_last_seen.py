"""user_proj_queue_status: add last_seen for span semantics

Reinterpret each row as a (first_seen, last_seen) span of unchanging
counts instead of a per-tick snapshot. The existing ``timestamp`` column
is now semantically *first_seen*; a new ``last_seen`` column records the
most recent tick at which the same ``(user, project, queue, counts)``
tuple was observed. Ingest coalesces identical adjacent ticks by
bumping ``last_seen`` instead of inserting a duplicate row.

Per design decision, existing rows are wiped — collectors will repopulate
under the new semantics. No backfill or compaction is attempted.

Cross-dialect notes:
  * The ALTER goes through ``batch_alter_table`` (mandatory for SQLite,
    benign on MySQL/Postgres).
  * Add the column nullable, populate via UPDATE, then alter to NOT NULL
    — the only pattern that works cleanly on every dialect when adding a
    NOT NULL column.

Revision ID: 0004_user_proj_queue_last_seen
Revises: 0003_user_proj_queue_status
Create Date: 2026-05-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_user_proj_queue_last_seen"
down_revision: Union[str, Sequence[str], None] = "0003_user_proj_queue_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wipe existing per-tick rows. The new ingest path coalesces by
    # comparing against the most recent tick's `last_seen`; mixing legacy
    # snapshot rows with new span rows would produce corrupted spans
    # until the legacy data aged out. Cleaner to start fresh.
    op.execute("DELETE FROM user_proj_queue_status")

    with op.batch_alter_table("user_proj_queue_status", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_seen", sa.DateTime(), nullable=True))

    # Defensive: any rows that survived the DELETE (concurrent inserts on
    # a live DB, or test fixtures pre-loaded between the two steps) get
    # last_seen seeded from timestamp so the NOT NULL alter below succeeds.
    op.execute("UPDATE user_proj_queue_status SET last_seen = timestamp WHERE last_seen IS NULL")

    with op.batch_alter_table("user_proj_queue_status", schema=None) as batch_op:
        batch_op.alter_column("last_seen", existing_type=sa.DateTime(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_user_proj_queue_status_last_seen"),
            ["last_seen"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("user_proj_queue_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_last_seen"))
        batch_op.drop_column("last_seen")
