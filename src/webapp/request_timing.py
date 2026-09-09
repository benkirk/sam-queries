"""Per-request DB-time instrumentation.

Accumulates SQL cursor-execute time and a query count onto ``flask.g`` so the
request log line can show ``db=<ms> q=<n>`` for the SAM MySQL engine and
``pgdb=<ms> pq=<n>`` for the plugin engines (job-history and fs-scans on CNPG).
The listeners are a no-op outside a Flask request context, so the separate
``create_sam_engine`` used by the CLI and scheduled tasks is unaffected.
"""

import time
import weakref

from flask import g, has_request_context
from sqlalchemy import event

#: bucket -> the ``flask.g`` attributes it accumulates into.
BUCKETS = {'db': ('db_ms', 'db_queries'), 'pgdb': ('pgdb_ms', 'pgdb_queries')}

# Engine -> bucket. Guards double registration (a plugin's get_engine is
# memoized, and tests build more than one app over one engine).
_INSTRUMENTED = weakref.WeakKeyDictionary()


def instrumented_bucket(engine):
    """The bucket *engine* reports into, or ``None`` if it is not instrumented."""
    return _INSTRUMENTED.get(engine)


def attach_query_timing(engine, bucket='db'):
    """Register cursor-execute listeners on *engine*, once per engine."""
    ms_attr, count_attr = BUCKETS[bucket]
    if engine in _INSTRUMENTED:
        return
    _INSTRUMENTED[engine] = bucket

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
            setattr(g, ms_attr, g.get(ms_attr, 0.0) + elapsed_ms)
            setattr(g, count_attr, g.get(count_attr, 0) + 1)


def init_request_timing(app, db):
    """Attach the ``db`` bucket to the Flask-SQLAlchemy default engine."""
    with app.app_context():
        engine = db.engine
    attach_query_timing(engine, 'db')
