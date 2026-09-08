"""Per-request DB-time instrumentation (webapp.request_timing).

Verifies the request log line carries `db=`/`q=`, and that the cursor listener
is a no-op outside a Flask request context (the CLI/task guard).
"""

import logging
import re

from sqlalchemy import text


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
        assert 'db=' in line and 'cpu=' in line and 'q=' in line, line
        # The projects page issues real queries.
        q = re.search(r'q=(\d+)', line)
        assert q and int(q.group(1)) >= 1, line

    def test_query_outside_request_context_does_not_raise(self, app):
        # after_cursor_execute guards on has_request_context(); a query in a bare
        # app context (no request) must accumulate nothing and not raise.
        from webapp.extensions import db
        with app.app_context():
            assert db.session.execute(text('SELECT 1')).scalar() == 1
