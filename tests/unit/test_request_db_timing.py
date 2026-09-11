"""Per-request timing (webapp.request_timing), attributed per logical database.

Verifies the request log line carries ``cpu=`` and one ``<label>=Xms/Nq`` per
database TOUCHED (sam / status / jobhistory / fsscans), that buckets do not bleed
into each other, that the plugin loaders label what they warm, the
``render_fields`` format contract (the single formatter for both log lines), and
that the cursor listener is a no-op outside a Flask request context (the CLI/task
guard).
"""

import logging
import re
from types import SimpleNamespace

from flask import Flask, g
from sqlalchemy import create_engine, text

from webapp.request_timing import (
    RequestProfile,
    attach_query_timing,
    instrumented_label,
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class TestRequestTimingLine:
    def test_log_line_carries_cpu_and_sam_query_time(self, app, auth_client):
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
        assert 'cpu=' in line, line
        # The projects page issues real SAM queries; TestingConfig loads no
        # plugin, so the sam bucket is present with a count and no plugin bucket is.
        m = re.search(r' sam=[0-9.]+ms/(\d+)q', line)
        assert m and int(m.group(1)) >= 1, line
        assert 'jobhistory=' not in line and 'fsscans=' not in line, line

    def test_query_outside_request_context_does_not_raise(self, app):
        # The cursor callback guards on RequestProfile.current(); a query in a
        # bare app context (no request) must accumulate nothing and not raise.
        from webapp.extensions import db
        with app.app_context():
            assert db.session.execute(text('SELECT 1')).scalar() == 1

    def test_sam_engine_is_labelled_sam(self, app):
        from webapp.extensions import db
        with app.app_context():
            assert instrumented_label(db.engine) == 'sam'


class TestPerDatabaseBuckets:
    def test_plugin_time_lands_in_its_label_not_sam(self, app):
        engine = create_engine('sqlite://')
        attach_query_timing(engine, 'jobhistory')
        attach_query_timing(engine, 'jobhistory')   # idempotent: no double count
        with app.test_request_context('/'):
            RequestProfile.start()
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            profile = g.request_profile
            assert profile.db['jobhistory'][1] == 1 and profile.db['jobhistory'][0] > 0
            assert 'sam' not in profile.db

    def test_job_history_loader_labels_what_it_warms(self):
        from webapp.jobs.session import JobHistoryExtension
        engine = create_engine('sqlite://')
        mod = SimpleNamespace(get_engine=lambda machine, pool_kwargs=None: engine)
        flask_app = Flask('t')
        flask_app.config['JOB_HISTORY_MACHINES'] = ['x']
        state = {'engines': {}}
        JobHistoryExtension()._warm(flask_app, mod, state)
        assert state['enabled'] and instrumented_label(engine) == 'jobhistory'

    def test_fs_scans_loader_labels_what_it_warms(self):
        from webapp.disk_scans.session import FsScansExtension
        engine = create_engine('sqlite://')
        mod = SimpleNamespace(list_pg_schemas=lambda database=None: ['c1'],
                              get_engine=lambda collection, database=None: engine)
        flask_app = Flask('t')
        state = {'databases': {}}
        FsScansExtension()._warm(flask_app, mod, state)
        assert state['enabled'] and instrumented_label(engine) == 'fsscans'


class TestRenderFieldsContract:
    """``render_fields`` is the single formatter for both log lines — pin its shape."""

    def test_touched_dbs_and_pool_are_presence_gated(self, app):
        with app.test_request_context('/'):
            p = RequestProfile.start()
            p.add_db('sam', 12.0)
            p.add_db('sam', 8.0)
            p.add_db('jobhistory', 30.0)
            p.add_pool(5.0)
            fields = p.render_fields()
        assert fields.startswith('cpu=')
        assert 'sam=20.0ms/2q' in fields
        assert 'jobhistory=30.0ms/1q' in fields
        assert 'pool=5.0ms' in fields
        # an untouched DB never appears.
        assert 'status=' not in fields and 'fsscans=' not in fields

    def test_sam_orders_first(self, app):
        with app.test_request_context('/'):
            p = RequestProfile.start()
            p.add_db('jobhistory', 1.0)
            p.add_db('sam', 1.0)
            fields = p.render_fields()
        assert fields.index('sam=') < fields.index('jobhistory=')

    def test_read_model_token_is_presence_gated_and_worst_wins(self, app):
        with app.test_request_context('/'):
            p = RequestProfile.start()
            assert 'rm=' not in p.render_fields()      # never consulted
            p.note_read_model('ok')
            assert p.render_fields().endswith('rm=served')
            p.note_read_model('ok-patched', patched=2)
            assert p.render_fields().endswith('rm=patched:2')
            p.note_read_model('no-rows')
            p.note_read_model('ok')
            assert p.render_fields().endswith('rm=live:no-rows')

    def test_actor_token_is_presence_gated_and_last(self, app):
        with app.test_request_context('/'):
            p = RequestProfile.start()
            assert 'who=' not in p.render_fields()      # anonymous: no token
            p.note_actor('user', 'benkirk')
            assert p.render_fields().endswith('who=user:benkirk')
            p.note_read_model('ok')
            fields = p.render_fields()
            assert fields.endswith('rm=served who=user:benkirk')
            p.note_actor('apikey', 'collector')
            assert p.render_fields().endswith('who=apikey:collector')
            p.note_actor('apikey', None)                # a missing name changes nothing
            assert p.render_fields().endswith('who=apikey:collector')

    def test_the_gate_verdict_reaches_the_profile(self, app):
        """The observer registered at init forwards every Lookup the gate returns."""
        from sam.queries.allocation_state import Lookup, _LOOKUP_OBSERVER
        assert _LOOKUP_OBSERVER is not None
        with app.test_request_context('/'):
            p = RequestProfile.start()
            _LOOKUP_OBSERVER(Lookup(None, 'disabled'))
            assert p.render_fields().endswith('rm=live:disabled')
        _LOOKUP_OBSERVER(Lookup({}, 'ok'))            # outside a request: no-op
