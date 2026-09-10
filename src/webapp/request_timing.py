"""Per-request timing, attributed by logical database.

One object, :class:`RequestProfile`, owns every timed dimension of a request —
wall ``total``, ``cpu``, per-database query time+count, connection ``pool`` wait
— and renders them onto the log line through a single formatter. Adding a
dimension is a field here plus a line in ``render_fields``, not edits scattered
across the request hooks, both log formats, and the watch parser.

Databases are named by ROLE, not backend engine: ``sam`` (SAM's own database),
``status`` (system_status), ``jobhistory`` and ``fsscans`` (the plugin stores).
The labels match the db-pool admin inventory and survive SAM moving to CNPG —
the point of the rename away from the old ``db``/``pgdb`` (MySQL-vs-Postgres) split.

The cursor listeners are a no-op outside a Flask request context, so the
``create_sam_engine`` used by the CLI and scheduled tasks is unaffected.
"""

import time
import weakref

from flask import g, has_request_context
from sqlalchemy import event

#: engine -> the logical DB label it reports into. Guards double registration
#: (a plugin's get_engine is memoized, and tests build more than one app over
#: one engine).
_INSTRUMENTED = weakref.WeakKeyDictionary()


def instrumented_label(engine):
    """The logical DB label *engine* reports into, or ``None`` if uninstrumented."""
    return _INSTRUMENTED.get(engine)


def timed_cursor_listeners(on_execute):
    """Return ``(before, after)`` cursor-execute listeners that bracket each
    statement with a reentry-safe ``perf_counter`` stack and call
    ``on_execute(elapsed_ms, statement, executemany)`` when it completes.

    The one implementation of the bracket, shared by the request profiler and
    the offline SQL counters (``tests/perf/_query_count.SQLStats`` and
    ``utils/profiling/``). Each call gets a UNIQUE ``conn.info`` key, so two
    listener sets attached to the same engine (e.g. the profiler and a perf
    test both on ``db.engine``) cannot pop each other's timestamps.
    """
    key = object()

    def before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault(key, []).append(time.perf_counter())

    def after(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get(key)
        if not stack:
            return
        elapsed_ms = (time.perf_counter() - stack.pop()) * 1000.0
        on_execute(elapsed_ms, statement, executemany)

    return before, after


class RequestProfile:
    """Timing accumulated on ``flask.g`` for one request, rendered onto its log line."""

    __slots__ = ('t_start', 'cpu_start', 'db', 'pool_ms')

    def __init__(self):
        self.t_start = time.monotonic()
        self.cpu_start = time.thread_time()   # per-thread CPU; gthread = 1 thread/request
        self.db = {}          # label -> [ms, count]
        self.pool_ms = 0.0

    @classmethod
    def start(cls):
        """Create the profile and store it at ``g.request_profile``."""
        profile = cls()
        g.request_profile = profile
        return profile

    @classmethod
    def current(cls):
        """The active request's profile, or ``None`` outside a request."""
        return g.get('request_profile') if has_request_context() else None

    def add_db(self, label, ms):
        slot = self.db.get(label)
        if slot is None:
            self.db[label] = [ms, 1]
        else:
            slot[0] += ms
            slot[1] += 1

    def add_pool(self, ms):
        self.pool_ms += ms

    def total_ms(self):
        return (time.monotonic() - self.t_start) * 1000.0

    def cpu_ms(self):
        return (time.thread_time() - self.cpu_start) * 1000.0

    def _db_items(self):
        # sam first, then the rest alphabetically — a stable order for the line.
        return sorted(self.db.items(), key=lambda kv: (kv[0] != 'sam', kv[0]))

    def render_fields(self):
        """The canonical token string for BOTH log lines.

        ``cpu=Xms`` then one ``label=Yms/Nq`` per database the request TOUCHED
        (presence-gated — untouched DBs never appear), then ``pool=`` when
        non-zero. ``rest`` (GIL/pool-not-attributed) is derived by the reader as
        ``total - cpu - Σdb - pool``, not emitted.
        """
        parts = ['cpu=%.1fms' % self.cpu_ms()]
        for label, (ms, count) in self._db_items():
            if count:
                parts.append('%s=%.1fms/%dq' % (label, ms, count))
        if self.pool_ms > 0:
            parts.append('pool=%.1fms' % self.pool_ms)
        return ' '.join(parts)


def _db_recorder(label):
    """A cursor callback that accumulates *label*'s time onto the active profile."""
    def record(elapsed_ms, statement, executemany):
        profile = RequestProfile.current()
        if profile is not None:
            profile.add_db(label, elapsed_ms)
    return record


def attach_query_timing(engine, label):
    """Register cursor-execute listeners on *engine* under logical DB *label*, once."""
    if engine in _INSTRUMENTED:
        return
    _INSTRUMENTED[engine] = label
    before, after = timed_cursor_listeners(_db_recorder(label))
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)


def init_request_timing(app, db):
    """Attach ``sam`` to the default engine and ``status`` to the system_status bind."""
    with app.app_context():
        attach_query_timing(db.engine, 'sam')
        status_engine = db.engines.get('system_status')
        if status_engine is not None:
            attach_query_timing(status_engine, 'status')
