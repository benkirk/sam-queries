"""user_proj_queue_status (Phase A)

Add per-user / per-project / per-queue rollup snapshots, with two new
denormalization lookup tables.

New tables:

    users               (user_id, username unique)
    project_codes       (project_code_id, project_code unique)
    user_proj_queue_status
        — same QueueRollupMetricsMixin counters as `queue_status`,
        — keyed by (timestamp, user_id, project_code_id, queue_id),
        — cascade-deletes from parent derecho_status / casper_status.

Additionally: refactors `queue_status` to share the rollup metric columns
via the `QueueRollupMetricsMixin`. **No schema change** to `queue_status`
— the mixin reuses the same column names/types. This migration only
*adds* tables.

Cross-dialect notes:
  * All ALTERs go through ``batch_alter_table`` (mandatory for SQLite,
    no-op-equivalent on MySQL/Postgres).
  * Lookup tables use bare ``CURRENT_TIMESTAMP`` server defaults.
  * Cut-over runbook:
    ``migrations/system_status/0003_USER_PROJ_QUEUE_RUNBOOK.md``.

Revision ID: 0003_user_proj_queue_status
Revises: 0002_normalize_lookups
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_user_proj_queue_status"
down_revision: Union[str, Sequence[str], None] = "0002_normalize_lookups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Lookup tables — UserDef, ProjectCodeDef.
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )

    op.create_table(
        "project_codes",
        sa.Column("project_code_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_code", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("project_code_id", name=op.f("pk_project_codes")),
        sa.UniqueConstraint("project_code", name=op.f("uq_project_codes_project_code")),
    )

    # ------------------------------------------------------------------
    # 2. Snapshot table — user_proj_queue_status.
    # ------------------------------------------------------------------
    op.create_table(
        "user_proj_queue_status",
        sa.Column("user_proj_queue_status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        # Parent FKs (cascade delete with parent snapshot).
        sa.Column("derecho_status_id", sa.Integer(), nullable=True),
        sa.Column("casper_status_id", sa.Integer(), nullable=True),
        # Lookup FKs.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_code_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        # Rollup metric columns (shared with queue_status via QueueRollupMetricsMixin).
        sa.Column("running_jobs", sa.Integer(), nullable=False),
        sa.Column("pending_jobs", sa.Integer(), nullable=False),
        sa.Column("held_jobs", sa.Integer(), nullable=False),
        sa.Column("cores_allocated", sa.Integer(), nullable=False),
        sa.Column("gpus_allocated", sa.Integer(), nullable=False),
        sa.Column("nodes_allocated", sa.Integer(), nullable=False),
        sa.Column("cores_pending", sa.Integer(), nullable=False),
        sa.Column("gpus_pending", sa.Integer(), nullable=False),
        sa.Column("cores_held", sa.Integer(), nullable=False),
        sa.Column("gpus_held", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["derecho_status_id"], ["derecho_status.status_id"],
            name=op.f("fk_user_proj_queue_status_derecho_status_id_derecho_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["casper_status_id"], ["casper_status.status_id"],
            name=op.f("fk_user_proj_queue_status_casper_status_id_casper_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"],
            name=op.f("fk_user_proj_queue_status_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["project_code_id"], ["project_codes.project_code_id"],
            name=op.f("fk_user_proj_queue_status_project_code_id_project_codes"),
        ),
        sa.ForeignKeyConstraint(
            ["system_id"], ["systems.system_id"],
            name=op.f("fk_user_proj_queue_status_system_id_systems"),
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"], ["queues.queue_id"],
            name=op.f("fk_user_proj_queue_status_queue_id_queues"),
        ),
        sa.PrimaryKeyConstraint("user_proj_queue_status_id",
                                name=op.f("pk_user_proj_queue_status")),
        # Snapshot uniqueness — system_id intentionally omitted (queue_id is
        # already system-scoped via the QueueDef.(system_id, name) unique key).
        sa.UniqueConstraint("timestamp", "user_id", "project_code_id", "queue_id",
                            name="uq_user_proj_queue_status_snapshot"),
    )
    with op.batch_alter_table("user_proj_queue_status", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_timestamp"),
                              ["timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_derecho_status_id"),
                              ["derecho_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_casper_status_id"),
                              ["casper_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_user_id"),
                              ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_project_code_id"),
                              ["project_code_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_system_id"),
                              ["system_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_proj_queue_status_queue_id"),
                              ["queue_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("user_proj_queue_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_queue_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_system_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_project_code_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_casper_status_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_derecho_status_id"))
        batch_op.drop_index(batch_op.f("ix_user_proj_queue_status_timestamp"))
    op.drop_table("user_proj_queue_status")
    op.drop_table("project_codes")
    op.drop_table("users")
