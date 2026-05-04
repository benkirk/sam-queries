"""normalize lookups (Phase 2)

Replace the denormalized text columns ``system_name`` / ``queue_name`` /
``filesystem_name`` / ``node_name`` / ``node_type`` on the snapshot
tables with FK references against four new lookup tables:

    systems       (system_id, name)
    queues        (queue_id, system_id, name)
    filesystems   (filesystem_id, name)
    login_nodes   (login_node_def_id, system_id, name, node_type)

Procedure (single migration, single transaction on Postgres; backup
required on MySQL — implicit-commit-on-DDL means a partial failure
leaves the schema torn):

  1. Create lookup tables
  2. Add nullable FK columns to dependent tables
  3. Backfill lookup tables from distinct text values
  4. Populate FK columns via correlated UPDATE
  5. Sanity-check no FK is NULL
  6. Tighten FK columns to NOT NULL, drop legacy text columns,
     rebuild unique constraints to use the FK ids

Cross-dialect notes:
  * All ALTERs go through ``batch_alter_table`` (mandatory for SQLite,
    no-op-equivalent on MySQL/Postgres).
  * UPDATEs use correlated subqueries (no ``UPDATE ... FROM`` /
    ``UPDATE ... JOIN``).
  * Lookup tables use bare ``CURRENT_TIMESTAMP`` server defaults
    (cross-dialect portable).
  * Cut-over runbook for active collectors:
    ``migrations/system_status/0002_NORMALIZATION_RUNBOOK.md``.

Revision ID: 0002_normalize_lookups
Revises: 0001_baseline
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_normalize_lookups"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Lightweight table descriptors used for op.bulk_insert. Migrations never
# import the live ORM (which moves with the codebase); these stand-alone
# descriptors are frozen at the migration's authoring time.
# ---------------------------------------------------------------------------
_systems_t = sa.table(
    "systems",
    sa.column("system_id", sa.Integer),
    sa.column("name", sa.String),
)
_queues_t = sa.table(
    "queues",
    sa.column("queue_id", sa.Integer),
    sa.column("system_id", sa.Integer),
    sa.column("name", sa.String),
)
_filesystems_t = sa.table(
    "filesystems",
    sa.column("filesystem_id", sa.Integer),
    sa.column("name", sa.String),
)
_login_nodes_t = sa.table(
    "login_nodes",
    sa.column("login_node_def_id", sa.Integer),
    sa.column("system_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("node_type", sa.String),
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create lookup tables.
    # ------------------------------------------------------------------
    op.create_table(
        "systems",
        sa.Column("system_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("system_id", name=op.f("pk_systems")),
        sa.UniqueConstraint("name", name=op.f("uq_systems_name")),
    )

    op.create_table(
        "queues",
        sa.Column("queue_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["system_id"], ["systems.system_id"],
            name=op.f("fk_queues_system_id_systems"),
        ),
        sa.PrimaryKeyConstraint("queue_id", name=op.f("pk_queues")),
        sa.UniqueConstraint("system_id", "name", name="uq_queues_system_id_name"),
    )
    with op.batch_alter_table("queues", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_queues_system_id"), ["system_id"], unique=False)

    op.create_table(
        "filesystems",
        sa.Column("filesystem_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("filesystem_id", name=op.f("pk_filesystems")),
        sa.UniqueConstraint("name", name=op.f("uq_filesystems_name")),
    )

    op.create_table(
        "login_nodes",
        sa.Column("login_node_def_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "node_type",
            sa.Enum("cpu", "gpu", "data-access",
                    name="login_node_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["system_id"], ["systems.system_id"],
            name=op.f("fk_login_nodes_system_id_systems"),
        ),
        sa.PrimaryKeyConstraint("login_node_def_id", name=op.f("pk_login_nodes")),
        sa.UniqueConstraint("system_id", "name", name="uq_login_nodes_system_id_name"),
    )
    with op.batch_alter_table("login_nodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_nodes_system_id"), ["system_id"], unique=False)

    # ------------------------------------------------------------------
    # 1b. MySQL: align lookup tables' collation with the existing snapshot
    #     tables. ``setup_status_db.py`` creates the system_status database
    #     with ``utf8mb4_unicode_ci``, so all pre-existing tables use it.
    #     Tables created by SQLAlchemy here would otherwise inherit MySQL
    #     8.0's default ``utf8mb4_0900_ai_ci``, which makes the
    #     correlated-subquery JOINs in the backfill UPDATEs below fail
    #     with "Illegal mix of collations". No-op on Postgres / SQLite.
    # ------------------------------------------------------------------
    if op.get_bind().dialect.name == "mysql":
        for tbl in ("systems", "queues", "filesystems", "login_nodes"):
            op.execute(
                f"ALTER TABLE {tbl} CONVERT TO CHARACTER SET utf8mb4 "
                f"COLLATE utf8mb4_unicode_ci"
            )

    # ------------------------------------------------------------------
    # 2. Add nullable FK columns to all dependent snapshot tables.
    # ------------------------------------------------------------------
    with op.batch_alter_table("queue_status", schema=None) as b:
        b.add_column(sa.Column("system_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("queue_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_queue_status_system_id_systems",
            "systems", ["system_id"], ["system_id"],
        )
        b.create_foreign_key(
            "fk_queue_status_queue_id_queues",
            "queues", ["queue_id"], ["queue_id"],
        )

    with op.batch_alter_table("filesystem_status", schema=None) as b:
        b.add_column(sa.Column("system_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("filesystem_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_filesystem_status_system_id_systems",
            "systems", ["system_id"], ["system_id"],
        )
        b.create_foreign_key(
            "fk_filesystem_status_filesystem_id_filesystems",
            "filesystems", ["filesystem_id"], ["filesystem_id"],
        )

    with op.batch_alter_table("login_node_status", schema=None) as b:
        b.add_column(sa.Column("system_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("login_node_def_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_login_node_status_system_id_systems",
            "systems", ["system_id"], ["system_id"],
        )
        b.create_foreign_key(
            "fk_login_node_status_login_node_def_id_login_nodes",
            "login_nodes", ["login_node_def_id"], ["login_node_def_id"],
        )

    with op.batch_alter_table("system_outages", schema=None) as b:
        b.add_column(sa.Column("system_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_system_outages_system_id_systems",
            "systems", ["system_id"], ["system_id"],
        )

    with op.batch_alter_table("resource_reservations", schema=None) as b:
        b.add_column(sa.Column("system_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_resource_reservations_system_id_systems",
            "systems", ["system_id"], ["system_id"],
        )

    # ------------------------------------------------------------------
    # 3. Backfill lookup tables from distinct text values across all
    #    contributing snapshot tables. UNION over all sources to avoid
    #    missing systems that only appear in (e.g.) outages.
    # ------------------------------------------------------------------
    bind = op.get_bind()

    distinct_systems = bind.execute(sa.text(
        "SELECT DISTINCT system_name FROM ("
        "  SELECT system_name FROM queue_status"
        "  UNION SELECT system_name FROM filesystem_status"
        "  UNION SELECT system_name FROM login_node_status"
        "  UNION SELECT system_name FROM system_outages"
        "  UNION SELECT system_name FROM resource_reservations"
        ") s WHERE system_name IS NOT NULL"
    )).scalars().all()
    if distinct_systems:
        op.bulk_insert(_systems_t, [{"name": n} for n in distinct_systems])

    distinct_filesystems = bind.execute(sa.text(
        "SELECT DISTINCT filesystem_name FROM filesystem_status "
        "WHERE filesystem_name IS NOT NULL"
    )).scalars().all()
    if distinct_filesystems:
        op.bulk_insert(_filesystems_t, [{"name": n} for n in distinct_filesystems])

    distinct_queues = bind.execute(sa.text(
        "SELECT DISTINCT system_name, queue_name FROM queue_status "
        "WHERE queue_name IS NOT NULL"
    )).all()
    for sys_name, q_name in distinct_queues:
        bind.execute(sa.text(
            "INSERT INTO queues (system_id, name) "
            "SELECT system_id, :q FROM systems WHERE name = :s"
        ), {"q": q_name, "s": sys_name})

    distinct_login_nodes = bind.execute(sa.text(
        "SELECT DISTINCT system_name, node_name, node_type FROM login_node_status "
        "WHERE node_name IS NOT NULL"
    )).all()
    for sys_name, n_name, n_type in distinct_login_nodes:
        bind.execute(sa.text(
            "INSERT INTO login_nodes (system_id, name, node_type) "
            "SELECT system_id, :n, :t FROM systems WHERE name = :s"
        ), {"n": n_name, "t": n_type, "s": sys_name})

    # ------------------------------------------------------------------
    # 4. Populate the new FK columns via correlated UPDATE
    #    (cross-dialect; works on MySQL, Postgres, SQLite).
    # ------------------------------------------------------------------
    bind.execute(sa.text(
        "UPDATE queue_status SET "
        "  system_id = (SELECT system_id FROM systems "
        "               WHERE name = queue_status.system_name), "
        "  queue_id  = (SELECT q.queue_id FROM queues q "
        "               JOIN systems s ON s.system_id = q.system_id "
        "               WHERE s.name = queue_status.system_name "
        "                 AND q.name = queue_status.queue_name)"
    ))
    bind.execute(sa.text(
        "UPDATE filesystem_status SET "
        "  system_id = (SELECT system_id FROM systems "
        "               WHERE name = filesystem_status.system_name), "
        "  filesystem_id = (SELECT filesystem_id FROM filesystems "
        "                   WHERE name = filesystem_status.filesystem_name)"
    ))
    bind.execute(sa.text(
        "UPDATE login_node_status SET "
        "  system_id = (SELECT system_id FROM systems "
        "               WHERE name = login_node_status.system_name), "
        "  login_node_def_id = (SELECT ln.login_node_def_id FROM login_nodes ln "
        "                       JOIN systems s ON s.system_id = ln.system_id "
        "                       WHERE s.name = login_node_status.system_name "
        "                         AND ln.name = login_node_status.node_name)"
    ))
    bind.execute(sa.text(
        "UPDATE system_outages SET system_id = ("
        "  SELECT system_id FROM systems WHERE name = system_outages.system_name"
        ")"
    ))
    bind.execute(sa.text(
        "UPDATE resource_reservations SET system_id = ("
        "  SELECT system_id FROM systems WHERE name = resource_reservations.system_name"
        ")"
    ))

    # ------------------------------------------------------------------
    # 5. Sanity-check that every row was successfully mapped. We treat
    #    "(table is empty)" the same as "(all rows mapped)" since the
    #    NULL count is zero either way.
    # ------------------------------------------------------------------
    for tbl, col in [
        ("queue_status", "system_id"),
        ("queue_status", "queue_id"),
        ("filesystem_status", "system_id"),
        ("filesystem_status", "filesystem_id"),
        ("login_node_status", "system_id"),
        ("login_node_status", "login_node_def_id"),
        ("system_outages", "system_id"),
        ("resource_reservations", "system_id"),
    ]:
        n = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NULL"
        )).scalar()
        if n:
            raise RuntimeError(
                f"backfill: {n} rows in {tbl}.{col} could not be mapped — "
                f"investigate before re-running the migration."
            )

    # ------------------------------------------------------------------
    # 6. Tighten FK columns to NOT NULL, drop legacy text columns,
    #    rebuild unique constraints. SQLite recreates the table for any
    #    of these — batch_alter_table handles the dance.
    # ------------------------------------------------------------------
    with op.batch_alter_table("queue_status", schema=None) as b:
        b.alter_column("system_id", existing_type=sa.Integer(), nullable=False)
        b.alter_column("queue_id", existing_type=sa.Integer(), nullable=False)
        b.drop_constraint("uq_system_queue_timestamp_name", type_="unique")
        b.drop_index("ix_queue_status_queue_name")
        b.drop_index("ix_queue_status_system_name")
        b.drop_column("queue_name")
        b.drop_column("system_name")
        b.create_index("ix_queue_status_system_id", ["system_id"], unique=False)
        b.create_index("ix_queue_status_queue_id", ["queue_id"], unique=False)
        b.create_unique_constraint(
            "uq_queue_status_timestamp_queue_id",
            ["timestamp", "queue_id"],
        )

    with op.batch_alter_table("filesystem_status", schema=None) as b:
        b.alter_column("system_id", existing_type=sa.Integer(), nullable=False)
        b.alter_column("filesystem_id", existing_type=sa.Integer(), nullable=False)
        b.drop_constraint("uq_fs_timestamp_name", type_="unique")
        b.drop_index("ix_filesystem_status_filesystem_name")
        b.drop_index("ix_filesystem_status_system_name")
        b.drop_column("filesystem_name")
        b.drop_column("system_name")
        b.create_index("ix_filesystem_status_system_id", ["system_id"], unique=False)
        b.create_index("ix_filesystem_status_filesystem_id", ["filesystem_id"], unique=False)
        b.create_unique_constraint(
            "uq_filesystem_status_timestamp_filesystem_id",
            ["timestamp", "filesystem_id"],
        )

    with op.batch_alter_table("login_node_status", schema=None) as b:
        b.alter_column("system_id", existing_type=sa.Integer(), nullable=False)
        b.alter_column("login_node_def_id", existing_type=sa.Integer(), nullable=False)
        b.drop_index("ix_login_node_status_node_name")
        b.drop_index("ix_login_node_status_system_name")
        b.drop_column("node_name")
        b.drop_column("system_name")
        b.drop_column("node_type")
        b.create_index("ix_login_node_status_system_id", ["system_id"], unique=False)
        b.create_index("ix_login_node_status_login_node_def_id",
                       ["login_node_def_id"], unique=False)

    with op.batch_alter_table("system_outages", schema=None) as b:
        b.alter_column("system_id", existing_type=sa.Integer(), nullable=False)
        b.drop_index("ix_outage_system_name")
        b.drop_index("ix_system_outages_system_name")
        b.drop_column("system_name")
        b.create_index("ix_system_outages_system_id", ["system_id"], unique=False)

    with op.batch_alter_table("resource_reservations", schema=None) as b:
        b.alter_column("system_id", existing_type=sa.Integer(), nullable=False)
        b.drop_index("ix_reservation_system_name")
        b.drop_index("ix_resource_reservations_system_name")
        b.drop_column("system_name")
        b.create_index("ix_resource_reservations_system_id", ["system_id"], unique=False)


def downgrade() -> None:
    """Reverse Phase 2: re-add text columns, backfill from lookup tables, drop FKs."""
    bind = op.get_bind()

    # 1. Re-add text columns as nullable.
    with op.batch_alter_table("queue_status", schema=None) as b:
        b.add_column(sa.Column("queue_name", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("system_name", sa.String(length=32), nullable=True))
    with op.batch_alter_table("filesystem_status", schema=None) as b:
        b.add_column(sa.Column("filesystem_name", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("system_name", sa.String(length=32), nullable=True))
    with op.batch_alter_table("login_node_status", schema=None) as b:
        b.add_column(sa.Column("node_name", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("system_name", sa.String(length=32), nullable=True))
        b.add_column(
            sa.Column(
                "node_type",
                sa.Enum("cpu", "gpu", "data-access",
                        name="login_node_type", native_enum=False),
                nullable=True,
            )
        )
    with op.batch_alter_table("system_outages", schema=None) as b:
        b.add_column(sa.Column("system_name", sa.String(length=64), nullable=True))
    with op.batch_alter_table("resource_reservations", schema=None) as b:
        b.add_column(sa.Column("system_name", sa.String(length=64), nullable=True))

    # 2. Backfill text columns from lookup tables via correlated UPDATEs.
    bind.execute(sa.text(
        "UPDATE queue_status SET "
        "  system_name = (SELECT name FROM systems WHERE system_id = queue_status.system_id), "
        "  queue_name  = (SELECT name FROM queues WHERE queue_id = queue_status.queue_id)"
    ))
    bind.execute(sa.text(
        "UPDATE filesystem_status SET "
        "  system_name     = (SELECT name FROM systems WHERE system_id = filesystem_status.system_id), "
        "  filesystem_name = (SELECT name FROM filesystems "
        "                     WHERE filesystem_id = filesystem_status.filesystem_id)"
    ))
    bind.execute(sa.text(
        "UPDATE login_node_status SET "
        "  system_name = (SELECT name FROM systems WHERE system_id = login_node_status.system_id), "
        "  node_name   = (SELECT name FROM login_nodes "
        "                 WHERE login_node_def_id = login_node_status.login_node_def_id), "
        "  node_type   = (SELECT node_type FROM login_nodes "
        "                 WHERE login_node_def_id = login_node_status.login_node_def_id)"
    ))
    bind.execute(sa.text(
        "UPDATE system_outages SET system_name = ("
        "  SELECT name FROM systems WHERE system_id = system_outages.system_id"
        ")"
    ))
    bind.execute(sa.text(
        "UPDATE resource_reservations SET system_name = ("
        "  SELECT name FROM systems WHERE system_id = resource_reservations.system_id"
        ")"
    ))

    # 3. Drop FK columns and rebuild legacy indexes / UKs.
    with op.batch_alter_table("resource_reservations", schema=None) as b:
        b.drop_index("ix_resource_reservations_system_id")
        b.drop_constraint("fk_resource_reservations_system_id_systems", type_="foreignkey")
        b.drop_column("system_id")
        b.alter_column("system_name", existing_type=sa.String(length=64), nullable=False)
        b.create_index("ix_resource_reservations_system_name", ["system_name"], unique=False)
        b.create_index("ix_reservation_system_name", ["system_name"], unique=False)

    with op.batch_alter_table("system_outages", schema=None) as b:
        b.drop_index("ix_system_outages_system_id")
        b.drop_constraint("fk_system_outages_system_id_systems", type_="foreignkey")
        b.drop_column("system_id")
        b.alter_column("system_name", existing_type=sa.String(length=64), nullable=False)
        b.create_index("ix_system_outages_system_name", ["system_name"], unique=False)
        b.create_index("ix_outage_system_name", ["system_name"], unique=False)

    with op.batch_alter_table("login_node_status", schema=None) as b:
        b.drop_index("ix_login_node_status_login_node_def_id")
        b.drop_index("ix_login_node_status_system_id")
        b.drop_constraint("fk_login_node_status_login_node_def_id_login_nodes", type_="foreignkey")
        b.drop_constraint("fk_login_node_status_system_id_systems", type_="foreignkey")
        b.drop_column("login_node_def_id")
        b.drop_column("system_id")
        b.alter_column("system_name", existing_type=sa.String(length=32), nullable=False)
        b.alter_column("node_name", existing_type=sa.String(length=32), nullable=False)
        b.alter_column("node_type",
                       existing_type=sa.Enum("cpu", "gpu", "data-access",
                                             name="login_node_type", native_enum=False),
                       nullable=False)
        b.create_index("ix_login_node_status_system_name", ["system_name"], unique=False)
        b.create_index("ix_login_node_status_node_name", ["node_name"], unique=False)

    with op.batch_alter_table("filesystem_status", schema=None) as b:
        b.drop_constraint("uq_filesystem_status_timestamp_filesystem_id", type_="unique")
        b.drop_index("ix_filesystem_status_filesystem_id")
        b.drop_index("ix_filesystem_status_system_id")
        b.drop_constraint("fk_filesystem_status_filesystem_id_filesystems", type_="foreignkey")
        b.drop_constraint("fk_filesystem_status_system_id_systems", type_="foreignkey")
        b.drop_column("filesystem_id")
        b.drop_column("system_id")
        b.alter_column("system_name", existing_type=sa.String(length=32), nullable=False)
        b.alter_column("filesystem_name", existing_type=sa.String(length=32), nullable=False)
        b.create_index("ix_filesystem_status_system_name", ["system_name"], unique=False)
        b.create_index("ix_filesystem_status_filesystem_name", ["filesystem_name"], unique=False)
        b.create_unique_constraint(
            "uq_fs_timestamp_name", ["timestamp", "filesystem_name"]
        )

    with op.batch_alter_table("queue_status", schema=None) as b:
        b.drop_constraint("uq_queue_status_timestamp_queue_id", type_="unique")
        b.drop_index("ix_queue_status_queue_id")
        b.drop_index("ix_queue_status_system_id")
        b.drop_constraint("fk_queue_status_queue_id_queues", type_="foreignkey")
        b.drop_constraint("fk_queue_status_system_id_systems", type_="foreignkey")
        b.drop_column("queue_id")
        b.drop_column("system_id")
        b.alter_column("system_name", existing_type=sa.String(length=32), nullable=False)
        b.alter_column("queue_name", existing_type=sa.String(length=32), nullable=False)
        b.create_index("ix_queue_status_system_name", ["system_name"], unique=False)
        b.create_index("ix_queue_status_queue_name", ["queue_name"], unique=False)
        b.create_unique_constraint(
            "uq_system_queue_timestamp_name",
            ["timestamp", "queue_name", "system_name"],
        )

    # 4. Drop lookup tables.
    with op.batch_alter_table("login_nodes", schema=None) as b:
        b.drop_index(b.f("ix_login_nodes_system_id"))
    op.drop_table("login_nodes")

    op.drop_table("filesystems")

    with op.batch_alter_table("queues", schema=None) as b:
        b.drop_index(b.f("ix_queues_system_id"))
    op.drop_table("queues")

    op.drop_table("systems")
