"""queues lookup: add PBS roster columns (queue_type, last_defined_at)

The collectors now capture the `qstat -Q -f -F json` queue roster and the
ingest path upserts it onto the write-once ``queues`` lookup. Snapshot rows
in ``queue_status`` only exist while jobs sit in a queue, so a routing queue
that drains instantly (e.g. casper's ``casper``) never appears there — the
roster columns are the durable "PBS still defines this queue" signal used
by the Admin Queue Cleanup cross-check.

Both columns are nullable: queues that predate roster collection simply
stay NULL. No data migration needed.

Revision ID: 0005_queue_def_roster_columns
Revises: 0004_user_proj_queue_last_seen
Create Date: 2026-07-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_queue_def_roster_columns"
down_revision: Union[str, Sequence[str], None] = "0004_user_proj_queue_last_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("queues", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "queue_type", sa.String(length=16), nullable=True,
            comment="PBS queue type as reported ('Execution' / 'Route')",
        ))
        batch_op.add_column(sa.Column(
            "last_defined_at", sa.DateTime(), nullable=True,
            comment="Most recent collector tick whose qstat -Q roster included this queue (naive-UTC)",
        ))


def downgrade() -> None:
    with op.batch_alter_table("queues", schema=None) as batch_op:
        batch_op.drop_column("last_defined_at")
        batch_op.drop_column("queue_type")
