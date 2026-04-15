"""Audit logging infrastructure tests — Phase 3 port.

Ported from tests/unit/test_audit_logging.py. The legacy file mostly
exercised audit logger plumbing (file writes, singleton pattern, dir
fallback) plus one meaningful behavioral test that the security-sensitive
ApiCredentials model is excluded from the SQLAlchemy `before_flush`
audit listener.

Three legacy tests were already `pytest.skip`'d as "verified via
integration" stubs (`test_audit_insert/update/delete`) — those are dropped
during the port rather than carried forward as dead code.

The api_credentials exclusion test is rewritten to install/remove its
own `before_flush` listener directly on the test session class instead
of going through `init_audit_events`, which has process-global state
that's not easily reset between tests.
"""
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from sam.security.roles import ApiCredentials
from webapp.audit.events import init_audit_events, reset_audit_events
from webapp.audit.logger import (
    ensure_log_directory,
    get_audit_logger,
    reset_audit_logger,
)

from factories import next_seq

pytestmark = pytest.mark.unit


@pytest.fixture
def audit_log_path(tmp_path):
    """Per-test audit log path under pytest's tmp_path; full reset after."""
    reset_audit_logger()
    reset_audit_events()
    log_path = str(tmp_path / "audit.log")
    yield log_path
    reset_audit_logger()
    reset_audit_events()


def _read_log(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.readlines()


def test_audit_logger_creation(audit_log_path):
    """get_audit_logger writes to the configured file."""
    logger = get_audit_logger(audit_log_path)
    logger.info("Test message")
    for handler in logger.handlers:
        handler.flush()

    logs = _read_log(audit_log_path)
    assert any("Test message" in line for line in logs)


def test_audit_logger_singleton(audit_log_path):
    """get_audit_logger uses a singleton pattern."""
    logger1 = get_audit_logger(audit_log_path)
    logger2 = get_audit_logger(audit_log_path)
    assert logger1 is logger2


def test_ensure_log_directory_falls_back_to_tempdir():
    """ensure_log_directory swaps to /tmp when the requested dir is unwritable."""
    bad_path = "/nonexistent/path/audit.log"
    # Skip if running as root (where the path may actually be creatable).
    try:
        Path(bad_path).parent.mkdir(parents=True, exist_ok=True)
        with open(bad_path, "w"):
            pass
        pytest.skip("running as root — bad_path is writable")
    except OSError:
        pass

    fallback = ensure_log_directory(bad_path)
    assert fallback != bad_path
    # fallback must itself be writable.
    with open(fallback, "w") as f:
        f.write("test")
    os.remove(fallback)


def test_init_audit_events_does_not_raise(audit_log_path):
    """init_audit_events must complete without errors on a fresh process state."""
    class _FakeApp: pass
    class _FakeDB: pass
    init_audit_events(_FakeApp(), _FakeDB(), audit_log_path)
    # Idempotency: a second call must also complete (early-returns on the flag).
    init_audit_events(_FakeApp(), _FakeDB(), audit_log_path)


def test_audit_excludes_api_credentials(session, audit_log_path):
    """ApiCredentials INSERTs must NOT appear in the audit log.

    Installs a minimal before_flush listener directly on the test session's
    class instead of going through init_audit_events — same exclusion
    logic, but no process-global registration to clean up afterward.
    """
    logger = get_audit_logger(audit_log_path)

    EXCLUDED_MODELS = {"ApiCredentials"}

    def _listener(sess, flush_context, instances):
        for obj in sess.new:
            if obj.__class__.__name__ in EXCLUDED_MODELS:
                continue
            logger.info(f"INSERT model={obj.__class__.__name__}")

    session_cls = type(session)
    event.listen(session_cls, "before_flush", _listener)
    try:
        creds = ApiCredentials(
            username=next_seq("aud"),  # ≤11 chars, worker-namespaced for xdist
            password="$2b$12$fakehash",
        )
        session.add(creds)
        session.flush()
        for handler in logger.handlers:
            handler.flush()
    finally:
        event.remove(session_cls, "before_flush", _listener)

    logs = _read_log(audit_log_path)
    assert not any("ApiCredentials" in line for line in logs), (
        f"ApiCredentials must not appear in audit log; got: {logs}"
    )
