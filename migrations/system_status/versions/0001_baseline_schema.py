"""baseline schema

Captures the system_status schema as it existed before Alembic was
introduced. Generated via `alembic revision --autogenerate` against an
empty SQLite, then manually cleaned:

  * Enums use ``native_enum=False`` to avoid the Postgres CREATE TYPE
    vs MySQL inline ENUM divergence (same behavior on SQLite).
  * ``server_default`` for created_at uses bare ``CURRENT_TIMESTAMP``
    (no parens) — works on MySQL, Postgres, and SQLite.
  * Pre-existing index redundancies (e.g. duplicate indexes on
    `system_outages.system_name` from both `index=True` and a separate
    `Index(...)` declaration) are preserved as-is so the baseline
    matches the current schema exactly. They will be reconciled in
    Phase 2 when the relevant text columns are dropped.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "casper_status",
        sa.Column("status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("viz_nodes_total", sa.Integer(), nullable=False),
        sa.Column("viz_nodes_available", sa.Integer(), nullable=False),
        sa.Column("viz_nodes_down", sa.Integer(), nullable=False),
        sa.Column("viz_nodes_reserved", sa.Integer(), nullable=False),
        sa.Column("viz_count_total", sa.Integer(), nullable=False),
        sa.Column("viz_count_allocated", sa.Integer(), nullable=False),
        sa.Column("viz_count_idle", sa.Integer(), nullable=False),
        sa.Column("viz_utilization_percent", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("cpu_nodes_total", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_available", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_down", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_reserved", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_total", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_available", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_down", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_reserved", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_total", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_allocated", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_idle", sa.Integer(), nullable=False),
        sa.Column("cpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("gpu_count_total", sa.Integer(), nullable=False),
        sa.Column("gpu_count_allocated", sa.Integer(), nullable=False),
        sa.Column("gpu_count_idle", sa.Integer(), nullable=False),
        sa.Column("gpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("memory_total_gb", sa.Float(), nullable=False),
        sa.Column("memory_allocated_gb", sa.Float(), nullable=False),
        sa.Column("memory_utilization_percent", sa.Float(), nullable=True),
        sa.Column("running_jobs", sa.Integer(), nullable=False),
        sa.Column("pending_jobs", sa.Integer(), nullable=False),
        sa.Column("held_jobs", sa.Integer(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("status_id", name=op.f("pk_casper_status")),
    )
    with op.batch_alter_table("casper_status", schema=None) as batch_op:
        batch_op.create_index("ix_casper_status_created_at", ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_casper_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "derecho_status",
        sa.Column("status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("cpu_nodes_total", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_available", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_down", sa.Integer(), nullable=False),
        sa.Column("cpu_nodes_reserved", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_total", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_available", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_down", sa.Integer(), nullable=False),
        sa.Column("gpu_nodes_reserved", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_total", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_allocated", sa.Integer(), nullable=False),
        sa.Column("cpu_cores_idle", sa.Integer(), nullable=False),
        sa.Column("cpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("gpu_count_total", sa.Integer(), nullable=False),
        sa.Column("gpu_count_allocated", sa.Integer(), nullable=False),
        sa.Column("gpu_count_idle", sa.Integer(), nullable=False),
        sa.Column("gpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("memory_total_gb", sa.Float(), nullable=False),
        sa.Column("memory_allocated_gb", sa.Float(), nullable=False),
        sa.Column("memory_utilization_percent", sa.Float(), nullable=True),
        sa.Column("running_jobs", sa.Integer(), nullable=False),
        sa.Column("pending_jobs", sa.Integer(), nullable=False),
        sa.Column("held_jobs", sa.Integer(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("status_id", name=op.f("pk_derecho_status")),
    )
    with op.batch_alter_table("derecho_status", schema=None) as batch_op:
        batch_op.create_index("ix_derecho_status_created_at", ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_derecho_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "jupyterhub_status",
        sa.Column("status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.Column("active_sessions", sa.Integer(), nullable=False),
        sa.Column("nodes_total", sa.Integer(), nullable=True),
        sa.Column("nodes_free", sa.Integer(), nullable=True),
        sa.Column("nodes_busy", sa.Integer(), nullable=True),
        sa.Column("nodes_down", sa.Integer(), nullable=True),
        sa.Column("cpus_total", sa.Integer(), nullable=True),
        sa.Column("cpus_free", sa.Integer(), nullable=True),
        sa.Column("cpus_used", sa.Integer(), nullable=True),
        sa.Column("cpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("gpus_total", sa.Integer(), nullable=True),
        sa.Column("gpus_free", sa.Integer(), nullable=True),
        sa.Column("gpus_used", sa.Integer(), nullable=True),
        sa.Column("gpu_utilization_percent", sa.Float(), nullable=True),
        sa.Column("memory_total_gb", sa.Float(), nullable=True),
        sa.Column("memory_free_gb", sa.Float(), nullable=True),
        sa.Column("memory_used_gb", sa.Float(), nullable=True),
        sa.Column("memory_utilization_percent", sa.Float(), nullable=True),
        sa.Column("jobs_running", sa.Integer(), nullable=True),
        sa.Column("casper_login_jobs", sa.Integer(), nullable=True),
        sa.Column("casper_batch_jobs", sa.Integer(), nullable=True),
        sa.Column("derecho_batch_jobs", sa.Integer(), nullable=True),
        sa.Column("jobs_suspended", sa.Integer(), nullable=True),
        sa.Column("nodes", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("status_id", name=op.f("pk_jupyterhub_status")),
    )
    with op.batch_alter_table("jupyterhub_status", schema=None) as batch_op:
        batch_op.create_index("ix_jupyterhub_status_created_at", ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_jupyterhub_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "resource_reservations",
        sa.Column("reservation_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("system_name", sa.String(length=64), nullable=False),
        sa.Column("reservation_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=True),
        sa.Column("partition", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("reservation_id", name=op.f("pk_resource_reservations")),
    )
    with op.batch_alter_table("resource_reservations", schema=None) as batch_op:
        batch_op.create_index("ix_reservation_start_time", ["start_time"], unique=False)
        batch_op.create_index("ix_reservation_system_name", ["system_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_resource_reservations_system_name"), ["system_name"], unique=False)

    op.create_table(
        "system_outages",
        sa.Column("outage_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("system_name", sa.String(length=64), nullable=False),
        sa.Column("component", sa.String(length=128), nullable=True),
        sa.Column(
            "severity",
            sa.Enum("critical", "major", "minor", "maintenance",
                    name="outage_severity", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("investigating", "identified", "monitoring", "resolved",
                    name="outage_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("estimated_resolution", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("outage_id", name=op.f("pk_system_outages")),
    )
    with op.batch_alter_table("system_outages", schema=None) as batch_op:
        batch_op.create_index("ix_outage_start_time", ["start_time"], unique=False)
        batch_op.create_index("ix_outage_status", ["status"], unique=False)
        batch_op.create_index("ix_outage_system_name", ["system_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_system_outages_system_name"), ["system_name"], unique=False)

    op.create_table(
        "casper_node_type_status",
        sa.Column("node_type_status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("casper_status_id", sa.Integer(), nullable=False, comment="FK to parent Casper status snapshot"),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("nodes_total", sa.Integer(), nullable=False),
        sa.Column("nodes_available", sa.Integer(), nullable=False),
        sa.Column("nodes_down", sa.Integer(), nullable=False),
        sa.Column("nodes_allocated", sa.Integer(), nullable=False),
        sa.Column("cores_per_node", sa.Integer(), nullable=True),
        sa.Column("memory_gb_per_node", sa.Integer(), nullable=True),
        sa.Column("gpu_model", sa.String(length=64), nullable=True),
        sa.Column("gpus_per_node", sa.Integer(), nullable=True),
        sa.Column("utilization_percent", sa.Float(), nullable=True),
        sa.Column("memory_utilization_percent", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["casper_status_id"], ["casper_status.status_id"],
            name=op.f("fk_casper_node_type_status_casper_status_id_casper_status"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_type_status_id", name=op.f("pk_casper_node_type_status")),
        sa.UniqueConstraint("timestamp", "node_type", name="uq_casper_nodetype_timestamp_type"),
    )
    with op.batch_alter_table("casper_node_type_status", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_casper_node_type_status_casper_status_id"), ["casper_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_casper_node_type_status_node_type"), ["node_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_casper_node_type_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "filesystem_status",
        sa.Column("fs_status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("derecho_status_id", sa.Integer(), nullable=True, comment="FK to parent Derecho status snapshot"),
        sa.Column("casper_status_id", sa.Integer(), nullable=True, comment="FK to parent Casper status snapshot"),
        sa.Column("filesystem_name", sa.String(length=32), nullable=False),
        sa.Column("system_name", sa.String(length=32), nullable=False, comment="System using this filesystem (derecho, casper, etc.)"),
        sa.Column("capacity_tb", sa.Float(), nullable=True),
        sa.Column("used_tb", sa.Float(), nullable=True),
        sa.Column("utilization_percent", sa.Float(), nullable=True),
        sa.Column("capacity_inodes", sa.Float(), nullable=True),
        sa.Column("used_inodes", sa.Float(), nullable=True),
        sa.Column("inodes_utilization_percent", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["casper_status_id"], ["casper_status.status_id"],
            name=op.f("fk_filesystem_status_casper_status_id_casper_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["derecho_status_id"], ["derecho_status.status_id"],
            name=op.f("fk_filesystem_status_derecho_status_id_derecho_status"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fs_status_id", name=op.f("pk_filesystem_status")),
        sa.UniqueConstraint("timestamp", "filesystem_name", name="uq_fs_timestamp_name"),
    )
    with op.batch_alter_table("filesystem_status", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_filesystem_status_casper_status_id"), ["casper_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_filesystem_status_derecho_status_id"), ["derecho_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_filesystem_status_filesystem_name"), ["filesystem_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_filesystem_status_system_name"), ["system_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_filesystem_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "login_node_status",
        sa.Column("login_node_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("derecho_status_id", sa.Integer(), nullable=True, comment="FK to parent Derecho status snapshot"),
        sa.Column("casper_status_id", sa.Integer(), nullable=True, comment="FK to parent Casper status snapshot"),
        sa.Column("node_name", sa.String(length=32), nullable=False, comment="Login node hostname (e.g., derecho1, derecho2)"),
        sa.Column(
            "node_type",
            sa.Enum("cpu", "gpu", "data-access", name="login_node_type", native_enum=False),
            nullable=False,
            comment="Node type - CPU or GPU enabled",
        ),
        sa.Column("system_name", sa.String(length=32), nullable=False, comment="System to which ths queue belongs (derecho, casper, etc.)"),
        sa.Column("user_count", sa.Integer(), nullable=True, comment="Current number of logged-in users"),
        sa.Column("num_cpus", sa.Integer(), nullable=True, comment="Total CPU count on node (from nproc --all); used to compute load percentages"),
        sa.Column("load_1min", sa.Float(), nullable=True, comment="1-minute load average as % of CPU capacity (raw_load / num_cpus * 100)"),
        sa.Column("load_5min", sa.Float(), nullable=True, comment="5-minute load average as % of CPU capacity (raw_load / num_cpus * 100)"),
        sa.Column("load_15min", sa.Float(), nullable=True, comment="15-minute load average as % of CPU capacity (raw_load / num_cpus * 100)"),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["casper_status_id"], ["casper_status.status_id"],
            name=op.f("fk_login_node_status_casper_status_id_casper_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["derecho_status_id"], ["derecho_status.status_id"],
            name=op.f("fk_login_node_status_derecho_status_id_derecho_status"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("login_node_id", name=op.f("pk_login_node_status")),
    )
    with op.batch_alter_table("login_node_status", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_node_status_casper_status_id"), ["casper_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_node_status_derecho_status_id"), ["derecho_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_node_status_node_name"), ["node_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_node_status_system_name"), ["system_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_node_status_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "queue_status",
        sa.Column("queue_status_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("derecho_status_id", sa.Integer(), nullable=True, comment="FK to parent Derecho status snapshot"),
        sa.Column("casper_status_id", sa.Integer(), nullable=True, comment="FK to parent Casper status snapshot"),
        sa.Column("queue_name", sa.String(length=32), nullable=False),
        sa.Column("system_name", sa.String(length=32), nullable=False, comment="System to which ths queue belongs (derecho, casper, etc.)"),
        sa.Column("running_jobs", sa.Integer(), nullable=False),
        sa.Column("pending_jobs", sa.Integer(), nullable=False),
        sa.Column("held_jobs", sa.Integer(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.Column("cores_allocated", sa.Integer(), nullable=False),
        sa.Column("gpus_allocated", sa.Integer(), nullable=False),
        sa.Column("nodes_allocated", sa.Integer(), nullable=False),
        sa.Column("cores_pending", sa.Integer(), nullable=False),
        sa.Column("gpus_pending", sa.Integer(), nullable=False),
        sa.Column("cores_held", sa.Integer(), nullable=False),
        sa.Column("gpus_held", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["casper_status_id"], ["casper_status.status_id"],
            name=op.f("fk_queue_status_casper_status_id_casper_status"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["derecho_status_id"], ["derecho_status.status_id"],
            name=op.f("fk_queue_status_derecho_status_id_derecho_status"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("queue_status_id", name=op.f("pk_queue_status")),
        sa.UniqueConstraint("timestamp", "queue_name", "system_name", name="uq_system_queue_timestamp_name"),
    )
    with op.batch_alter_table("queue_status", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_queue_status_casper_status_id"), ["casper_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_queue_status_derecho_status_id"), ["derecho_status_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_queue_status_queue_name"), ["queue_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_queue_status_system_name"), ["system_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_queue_status_timestamp"), ["timestamp"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("queue_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_queue_status_timestamp"))
        batch_op.drop_index(batch_op.f("ix_queue_status_system_name"))
        batch_op.drop_index(batch_op.f("ix_queue_status_queue_name"))
        batch_op.drop_index(batch_op.f("ix_queue_status_derecho_status_id"))
        batch_op.drop_index(batch_op.f("ix_queue_status_casper_status_id"))
    op.drop_table("queue_status")

    with op.batch_alter_table("login_node_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_node_status_timestamp"))
        batch_op.drop_index(batch_op.f("ix_login_node_status_system_name"))
        batch_op.drop_index(batch_op.f("ix_login_node_status_node_name"))
        batch_op.drop_index(batch_op.f("ix_login_node_status_derecho_status_id"))
        batch_op.drop_index(batch_op.f("ix_login_node_status_casper_status_id"))
    op.drop_table("login_node_status")

    with op.batch_alter_table("filesystem_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filesystem_status_timestamp"))
        batch_op.drop_index(batch_op.f("ix_filesystem_status_system_name"))
        batch_op.drop_index(batch_op.f("ix_filesystem_status_filesystem_name"))
        batch_op.drop_index(batch_op.f("ix_filesystem_status_derecho_status_id"))
        batch_op.drop_index(batch_op.f("ix_filesystem_status_casper_status_id"))
    op.drop_table("filesystem_status")

    with op.batch_alter_table("casper_node_type_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_casper_node_type_status_timestamp"))
        batch_op.drop_index(batch_op.f("ix_casper_node_type_status_node_type"))
        batch_op.drop_index(batch_op.f("ix_casper_node_type_status_casper_status_id"))
    op.drop_table("casper_node_type_status")

    with op.batch_alter_table("system_outages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_system_outages_system_name"))
        batch_op.drop_index("ix_outage_system_name")
        batch_op.drop_index("ix_outage_status")
        batch_op.drop_index("ix_outage_start_time")
    op.drop_table("system_outages")

    with op.batch_alter_table("resource_reservations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resource_reservations_system_name"))
        batch_op.drop_index("ix_reservation_system_name")
        batch_op.drop_index("ix_reservation_start_time")
    op.drop_table("resource_reservations")

    with op.batch_alter_table("jupyterhub_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jupyterhub_status_timestamp"))
        batch_op.drop_index("ix_jupyterhub_status_created_at")
    op.drop_table("jupyterhub_status")

    with op.batch_alter_table("derecho_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_derecho_status_timestamp"))
        batch_op.drop_index("ix_derecho_status_created_at")
    op.drop_table("derecho_status")

    with op.batch_alter_table("casper_status", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_casper_status_timestamp"))
        batch_op.drop_index("ix_casper_status_created_at")
    op.drop_table("casper_status")
