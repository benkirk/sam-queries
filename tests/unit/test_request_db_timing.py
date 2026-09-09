"""Per-request DB-time instrumentation (webapp.request_timing).

Verifies the request log line carries `db=`/`q=` (SAM engine) and `pgdb=`/`pq=`
(plugin engines), that the two buckets do not bleed into each other, that both
plugin loaders instrument what they warm, and that the cursor listener is a
no-op outside a Flask request context (the CLI/task guard).
"""

import logging
import re
from types import SimpleNamespace

from flask import Flask, g
from sqlalchemy import create_engine, text

from webapp.request_timing import attach_query_timing, instrumented_bucket


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class TestRequestDbTiming:
    def test_log_line_carries_db_time_and_query_count(self, app, auth_client):
        # app.logger has propagate=False, so attach a handler directly.
        cap = _Capture()
        app.logger.addHandler(cap)
        app.logger.setLevel(logging.INFO)
        try:
            resp = auth_client.get('/allocations/projects')
        finally:
            app.logger.removeHandler(cap)

        assert resp.status_code == 200
        line = next((m for m in cap.messages if '/allocations/projects' in m), None)
        assert line is not None, f'no request log line captured: {cap.messages}'
        assert 'db=' in line and 'cpu=' in line and 'pgdb=' in line, line
        # The projects page issues real SAM queries; TestingConfig loads no plugin.
        q = re.search(r' q=(\d+) pq=(\d+)', line)
        assert q and int(q.group(1)) >= 1 and int(q.group(2)) == 0, line

    def test_query_outside_request_context_does_not_raise(self, app):
        # after_cursor_execute guards on has_request_context(); a query in a bare
        # app context (no request) must accumulate nothing and not raise.
        from webapp.extensions import db
        with app.app_context():
            assert db.session.execute(text('SELECT 1')).scalar() == 1


class TestPluginBucket:
    def test_plugin_engine_time_lands_in_pgdb_not_db(self, app):
        engine = create_engine('sqlite://')
        attach_query_timing(engine, 'pgdb')
        attach_query_timing(engine, 'pgdb')   # idempotent: no double count
        with app.test_request_context('/'):
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            assert g.get('pgdb_queries') == 1 and g.get('pgdb_ms') > 0
            assert g.get('db_queries', 0) == 0 and g.get('db_ms', 0.0) == 0.0

    def test_job_history_loader_instruments_what_it_warms(self):
        from webapp.jobs.session import JobHistoryExtension
        engine = create_engine('sqlite://')
        mod = SimpleNamespace(get_engine=lambda machine, pool_kwargs=None: engine)
        flask_app = Flask('t')
        flask_app.config['JOB_HISTORY_MACHINES'] = ['x']
        state = {'engines': {}}
        JobHistoryExtension()._warm(flask_app, mod, state)
        assert state['enabled'] and instrumented_bucket(engine) == 'pgdb'

    def test_fs_scans_loader_instruments_what_it_warms(self):
        from webapp.disk_scans.session import FsScansExtension
        engine = create_engine('sqlite://')
        mod = SimpleNamespace(list_pg_schemas=lambda database=None: ['c1'],
                              get_engine=lambda collection, database=None: engine)
        flask_app = Flask('t')
        state = {'databases': {}}
        FsScansExtension()._warm(flask_app, mod, state)
        assert state['enabled'] and instrumented_bucket(engine) == 'pgdb'
