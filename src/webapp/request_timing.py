"""Per-request DB-time instrumentation.

Accumulates SQL cursor-execute time and a query count onto ``flask.g`` so the
request log line can show ``db=<ms> q=<n>`` — making the app-vs-DB split visible
per request. Attaches to the Flask-SQLAlchemy default engine only; the listener
is a no-op outside a Flask request context, so the separate ``create_sam_engine``
used by the CLI and scheduled tasks is unaffected.
"""

import time

from flask import g, has_request_context
from sqlalchemy import event

# Prevent double-registration under parallel testing (mirrors audit/events.py).
_TIMING_REGISTERED = False


def init_request_timing(app, db):
    """Register cursor-execute listeners on the default SAM engine (once)."""
    global _TIMING_REGISTERED
    if _TIMING_REGISTERED:
        return

    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, 'before_cursor_execute')
    def _before(conn, cursor, statement, parameters, context, executemany):
        # Stack, not scalar: a savepoint/nested execute can reenter.
        conn.info.setdefault('_q_start', []).append(time.perf_counter())

    @event.listens_for(engine, 'after_cursor_execute')
    def _after(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get('_q_start')
        if not stack:
            return
        elapsed_ms = (time.perf_counter() - stack.pop()) * 1000.0
        # g.get(...) defaults cover the aborted-before-_set_request_id case.
        if has_request_context():
            g.db_ms = g.get('db_ms', 0.0) + elapsed_ms
            g.db_queries = g.get('db_queries', 0) + 1

    _TIMING_REGISTERED = True
