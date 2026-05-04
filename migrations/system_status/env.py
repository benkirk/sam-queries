"""Alembic environment for the `system_status` database.

Connection-URL resolution order:
  1. ALEMBIC_SYSTEM_STATUS_URL (set by tests / one-off invocations)
  2. system_status.session.connection_string (built from STATUS_DB_* env vars)

We import `connection_string` rather than re-implementing the MySQL/Postgres
dispatch and SSL handling that already lives in
`src/system_status/session/__init__.py`. FLASK_ACTIVE is unset before
importing models so `StatusBase` resolves to a plain declarative_base
carrying the naming-convention metadata.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


# ---------------------------------------------------------------------------
# Locate src/ so we can `import system_status` without an editable install.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Force standalone resolution of StatusBase. Without this, importing
# `system_status.base` inside a Flask-active shell would bind `StatusBase`
# to `db.Model`, whose metadata lacks our naming convention.
# ---------------------------------------------------------------------------
os.environ.pop("FLASK_ACTIVE", None)


# ---------------------------------------------------------------------------
# Resolve the database URL.
# ---------------------------------------------------------------------------
_override_url = os.getenv("ALEMBIC_SYSTEM_STATUS_URL")
if _override_url:
    _DB_URL = _override_url
else:
    # Lazy import — module-import side-effects rely on STATUS_DB_* env vars.
    from system_status.session import connection_string  # type: ignore[import-not-found]
    # NOTE: SQLAlchemy's URL.__str__ redacts the password (replaces it
    # with `***`); engine_from_config would then try to authenticate
    # with the literal "***". `render_as_string(hide_password=False)`
    # emits the real, URL-encoded password.
    _DB_URL = connection_string.render_as_string(hide_password=False)


# ---------------------------------------------------------------------------
# Import all models so target_metadata is populated.
# ---------------------------------------------------------------------------
import system_status.models  # noqa: F401  populates StatusBase.metadata
from system_status import StatusBase  # noqa: E402

target_metadata = StatusBase.metadata


# ---------------------------------------------------------------------------
# Alembic config / logging.
# ---------------------------------------------------------------------------
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the resolved URL into the alembic config so engine_from_config picks it up.
config.set_main_option("sqlalchemy.url", _DB_URL)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Useful for the prod stamp procedure and DBA review:
        alembic upgrade head --sql > out.sql
    """
    context.configure(
        url=_DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Execute migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # render_as_batch is unconditional: required for SQLite ALTERs
            # used by the test suite; a no-op-equivalent for MySQL/Postgres.
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
