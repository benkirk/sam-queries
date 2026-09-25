"""/database browser: access gate, rendering, redaction, and the no-COUNT page path."""
import re

import pytest
from flask import url_for
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from webapp.utils import rbac
from webapp.utils.rbac import ALL_VIEW, Permission


def _url(app, endpoint, **kw):
    with app.test_request_context():
        return url_for(endpoint, **kw)


def _every_rule(app):
    """(method, url) for every db_browser rule, with placeholder arguments."""
    out = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('db_browser.'):
            kw = {a: {'source': 'sam', 'table': 'users'}[a] for a in rule.arguments}
            for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
                out.append((method, _url(app, rule.endpoint, **kw)))
    return out


@pytest.fixture
def benkirk_row_url(app, session):
    from sam import User
    uid = User.get_by_username(session, 'benkirk').user_id
    return _url(app, 'db_browser.row', source='sam', table='users', **{'k.user_id': uid})


def _override(monkeypatch, client, perms):
    """Give the non-admin client's user exactly ``perms``."""
    with client.session_transaction() as sess:
        uid = int(sess['_user_id'])
    from sam import User
    from webapp.extensions import db
    with client.application.app_context():
        username = db.session.get(User, uid).username
    monkeypatch.setitem(rbac.USER_PERMISSION_OVERRIDES, username, set(perms))


class TestGate:

    def test_anonymous_redirects_to_login(self, app, client):
        resp = client.get(_url(app, 'db_browser.index'))
        assert resp.status_code == 302 and '/auth/login' in resp.headers['Location']

    def test_anonymous_htmx_gets_hx_redirect(self, app, client):
        resp = client.get(_url(app, 'db_browser.index'), headers={'HX-Request': 'true'})
        assert resp.status_code == 401 and resp.headers['HX-Redirect']

    def test_every_rule_is_403_without_the_permission(self, app, non_admin_client):
        rules = _every_rule(app)
        assert len(rules) >= 9
        for method, url in rules:
            resp = non_admin_client.open(url, method=method)
            assert resp.status_code == 403, (method, url)

    def test_all_view_plus_admin_dashboard_is_not_enough(self, app, non_admin_client, monkeypatch):
        _override(monkeypatch, non_admin_client, ALL_VIEW | {Permission.ACCESS_ADMIN_DASHBOARD})
        assert non_admin_client.get(_url(app, 'db_browser.index')).status_code == 403


class TestPages:

    @pytest.mark.parametrize('endpoint, kw', [
        ('db_browser.index', {}),
        ('db_browser.source', {'source': 'sam'}),
        ('db_browser.source', {'source': 'system_status'}),
        ('db_browser.table', {'source': 'sam', 'table': 'users'}),
        ('db_browser.table', {'source': 'sam', 'table': 'comp_activity_charge'}),   # a view
        ('db_browser.table_schema', {'source': 'sam', 'table': 'users'}),
        ('db_browser.tables_fragment', {'source': 'sam', 'q': 'user'}),
    ])
    def test_renders(self, app, auth_client, endpoint, kw):
        resp = auth_client.get(_url(app, endpoint, **kw))
        assert resp.status_code == 200
        assert resp.headers['Cache-Control'] == 'private, no-store'

    def test_filtered_table(self, app, auth_client):
        resp = auth_client.get(_url(app, 'db_browser.table', source='sam', table='users',
                                    **{'f0.col': 'username', 'f0.op': 'eq', 'f0.v': 'benkirk'}))
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert '>benkirk<' in html and '1 row by user_id' in html

    def test_row_page_links_back_to_referencing_tables(self, auth_client, benkirk_row_url):
        html = auth_client.get(benkirk_row_url).get_data(as_text=True)
        assert 'account_user.user_id' in html and 'Open in SAM' in html

    def test_count_and_cell_fragments(self, app, auth_client, session):
        from sam import User
        uid = User.get_by_username(session, 'benkirk').user_id
        count = auth_client.get(_url(app, 'db_browser.count_fragment', source='sam', table='users',
                                     **{'f0.col': 'username', 'f0.op': 'eq', 'f0.v': 'benkirk'}))
        assert '1 row' in count.get_data(as_text=True)
        cell = auth_client.get(_url(app, 'db_browser.cell_fragment', source='sam', table='users',
                                    col='username', **{'k.user_id': uid}))
        assert 'benkirk' in cell.get_data(as_text=True)

    def test_top_values_fragment_links_back_with_the_value_filter(self, app, auth_client):
        html = auth_client.get(_url(app, 'db_browser.values_fragment', source='sam', table='users',
                                    col='username', **{'f0.col': 'username', 'f0.op': 'like',
                                                       'f0.v': 'benkirk'})).get_data(as_text=True)
        assert 'within the current filters' in html
        assert 'f1.col=username&amp;f1.op=eq&amp;f1.v=benkirk' in html

    @pytest.mark.parametrize('col', ['no_such_column', 'password'])
    def test_top_values_refuses_unknown_and_redacted_columns(self, app, auth_client, col):
        table = 'api_credentials' if col == 'password' else 'users'
        assert auth_client.get(_url(app, 'db_browser.values_fragment', source='sam', table=table,
                                    col=col)).status_code == 404

    def test_messy_form_submission_redirects_to_canonical(self, app, auth_client):
        url = _url(app, 'db_browser.table', source='sam', table='users',
                   **{'f0.col': 'username', 'f0.op': 'eq', 'f0.v': 'benkirk',
                      'f1.col': '', 'f1.op': 'eq', 'f1.v': '', 'per_page': 50})
        resp = auth_client.get(url)
        assert resp.status_code == 302
        assert 'f1' not in resp.headers['Location'] and 'per_page' not in resp.headers['Location']

    def test_bad_filter_value_is_inline_not_500(self, app, auth_client):
        resp = auth_client.get(_url(app, 'db_browser.table', source='sam', table='users',
                                    **{'f0.col': 'user_id', 'f0.op': 'gt', 'f0.v': 'abc'}))
        assert resp.status_code == 200
        assert 'is-invalid' in resp.get_data(as_text=True)

    @pytest.mark.parametrize('source, table', [
        ('sam', 'no_such_table'), ('nowhere', 'users'), ('sam', 'users; DROP TABLE users'),
    ])
    def test_unknown_names_404(self, app, auth_client, source, table):
        assert auth_client.get(
            _url(app, 'db_browser.table', source=source, table=table)).status_code == 404

    def test_bare_table_name_redirects_to_sam(self, app, auth_client):
        resp = auth_client.get(_url(app, 'db_browser.source', source='users'))
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith(
            _url(app, 'db_browser.table', source='sam', table='users'))

    def test_timeout_renders_a_message(self, app, auth_client, monkeypatch):
        class _Timeout(Exception):
            pgcode = '57014'

        def boom(*a, **kw):
            raise DBAPIError('SELECT', {}, _Timeout('canceling statement'))
        monkeypatch.setattr('webapp.db_browser.routes.fetch_page', boom)
        resp = auth_client.get(_url(app, 'db_browser.table', source='sam', table='users'))
        assert resp.status_code == 200 and 's limit' in resp.get_data(as_text=True)

    def test_refresh_clears_the_source_cache(self, app, auth_client):
        from webapp.db_browser.sources import CACHE
        auth_client.get(_url(app, 'db_browser.source', source='sam'))
        assert any(k[0] == 'sam' for k in CACHE._data)
        resp = auth_client.post(_url(app, 'db_browser.refresh', source='sam'))
        assert resp.status_code == 302
        assert not any(k[0] == 'sam' for k in CACHE._data)


class TestRedaction:

    def test_password_column_is_never_shown_or_filterable(self, app, auth_client):
        html = auth_client.get(_url(app, 'db_browser.table', source='sam',
                                    table='api_credentials')).get_data(as_text=True)
        assert not re.search(r'<th[^>]*>\s*<a[^>]*>\s*password', html)
        assert '<option value="password"' not in html
        resp = auth_client.get(_url(app, 'db_browser.table', source='sam', table='api_credentials',
                                    **{'f0.col': 'password', 'f0.op': 'like', 'f0.v': '$2b$%'}))
        assert 'unknown column' in resp.get_data(as_text=True)

    def test_raw_payload_needs_manage_xras(self, app, non_admin_client, monkeypatch):
        _override(monkeypatch, non_admin_client, {Permission.ADMIN_DATABASE})
        html = non_admin_client.get(_url(app, 'db_browser.table', source='sam',
                                         table='xras_action_log')).get_data(as_text=True)
        assert '<option value="raw_payload"' not in html
        _override(monkeypatch, non_admin_client, {Permission.ADMIN_DATABASE, Permission.MANAGE_XRAS})
        html = non_admin_client.get(_url(app, 'db_browser.table', source='sam',
                                         table='xras_action_log')).get_data(as_text=True)
        assert '<option value="raw_payload"' in html


def test_warm_page_runs_one_select_and_no_count(app, auth_client):
    from webapp.extensions import db
    url = _url(app, 'db_browser.table', source='sam', table='comp_charge_summary')
    auth_client.get(url)   # warm the metadata cache
    statements = []
    with app.app_context():
        engine = db.engine

    def capture(conn, cursor, statement, *a):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        assert auth_client.get(url).status_code == 200
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not [s for s in statements if 'count(' in s.lower()]
    assert len([s for s in statements if ' AS c0' in s]) == 1
