"""user_last_seen — when each user was last seen, per access source.

Two new tables, nothing existing touched, so the downgrade is a clean DROP and
the upgrade can land ahead of the code that writes them.

``access_sources`` is a (kind, system) lookup; ``user_last_seen`` keeps one
row per (user, source) with a composite primary key. Neither is a snapshot
table: retention must never prune them. Timestamps are naive UTC.

Revision ID: 0007_user_last_seen
Revises: 0006_task_run
Create Date: 2026-09-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_last_seen"
down_revision: Union[str, None] = "0006_task_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_sources",
        sa.Column("source_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["system_id"], ["systems.system_id"],
                                name=op.f("fk_access_sources_system_id_systems")),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_access_sources")),
        sa.UniqueConstraint("kind", "system_id", name="uq_access_sources_kind_system_id"),
    )
    with op.batch_alter_table("access_sources", schema=None) as batch_op:
        batch_op.create_index("ix_access_sources_system_id", ["system_id"], unique=False)

    op.create_table(
        "user_last_seen",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["status_users.user_id"],
                                name=op.f("fk_user_last_seen_user_id_status_users")),
        sa.ForeignKeyConstraint(["source_id"], ["access_sources.source_id"],
                                name=op.f("fk_user_last_seen_source_id_access_sources")),
        sa.PrimaryKeyConstraint("user_id", "source_id", name=op.f("pk_user_last_seen")),
    )
    with op.batch_alter_table("user_last_seen", schema=None) as batch_op:
        batch_op.create_index("ix_user_last_seen_source_id_last_seen",
                              ["source_id", "last_seen"], unique=False)


def downgrade() -> None:
    # Children first; DROP TABLE takes its indexes with it (see 0003's downgrade).
    op.drop_table("user_last_seen")
    op.drop_table("access_sources")
