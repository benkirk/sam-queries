"""task_run — the scheduled-task ledger.

Creates one new table and touches nothing existing, so there is no data
migration and the downgrade is a clean DROP.

The table records one row per (task, occurrence). Its UNIQUE constraint on
``(task_name, occurrence_key)`` is the whole concurrency design: it is the
dedup key and the mutual-exclusion lock at once, so two dispatchers racing for
the same slot resolve by one of them catching an IntegrityError. Everything
else here — the state column, the attempt counter, the heartbeat — exists to
make a crashed run recoverable rather than permanently locked.

All timestamps are naive **UTC**, matching the rest of this bind (SAM MySQL is
naive-Mountain; `system_status` is not).

No FKs. A task name is a registry key, not a row in a table, and the ledger
must outlive any entity a task happens to touch.

Revision ID: 0006_task_run
Revises: 0005_queue_def_roster_columns
Create Date: 2026-08-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_task_run"
down_revision: Union[str, None] = "0005_queue_def_roster_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_run",
        sa.Column("task_run_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_name", sa.String(length=64), nullable=False),
        sa.Column("occurrence_key", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        # `trigger` is reserved in both MySQL and Postgres; see the model.
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("runner_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("task_run_id", name=op.f("pk_task_run")),
        # The claim. Not merely a uniqueness nicety — the INSERT racing against
        # this constraint IS how mutual exclusion is achieved, portably, on
        # MySQL, Postgres and SQLite alike.
        sa.UniqueConstraint("task_name", "occurrence_key",
                            name="uq_task_run_task_name_occurrence_key"),
    )
    with op.batch_alter_table("task_run", schema=None) as batch_op:
        # "last run of X" and the --history listing.
        batch_op.create_index("ix_task_run_task_name_claimed_at",
                              ["task_name", "claimed_at"], unique=False)
        # The stale sweep: find rows still `running` past their lease.
        batch_op.create_index("ix_task_run_state", ["state"], unique=False)


def downgrade() -> None:
    # DROP TABLE removes the table's indexes and constraints in a single
    # atomic operation on every dialect. Explicit drop_index() first is the
    # pattern that breaks on MySQL — see 0003's downgrade for the same note.
    op.drop_table("task_run")
