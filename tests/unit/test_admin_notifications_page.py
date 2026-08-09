"""The notification delivery log page.

The gate this module exists for is the **negative** permission case:
`VIEW_SYSTEM_CONFIG` gets the Configuration tile and must be 403'd off this
page, because that boundary is the only thing keeping recipient addresses off
the lower tier.
"""

import pytest

from webapp.utils.rbac import Permission

PAGE = '/admin/htmx/notifications'
LOG = '/admin/htmx/notifications/log'
DETAIL = '/admin/htmx/notifications/1'


@pytest.fixture
def config_only_client(auth_client, monkeypatch):
    """`benkirk` with VIEW_SYSTEM_CONFIG but *without* SYSTEM_ADMIN.

    The exact shape this page must refuse: entitled to the counts on the
    Configuration tile, not to the rows that name people.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_system_admin(user):
        kept = {p for p in real(user) if p is not Permission.SYSTEM_ADMIN}
        kept.add(Permission.VIEW_SYSTEM_CONFIG)
        return kept

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_system_admin)
    return auth_client


class TestThePermissionBoundary:
    """One tier apart, deliberately — and the gate is on the ROUTE, so a
    view-source cannot reveal what the page chose not to draw."""

    @pytest.mark.parametrize('path', [PAGE, LOG, DETAIL])
    def test_view_system_config_alone_is_refused(self, config_only_client, path):
        assert config_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [PAGE, LOG, DETAIL])
    def test_anonymous_is_refused(self, client, path):
        assert client.get(path).status_code in (302, 401, 403)

    def test_the_configuration_tile_is_still_reachable_at_the_lower_tier(
            self, config_only_client):
        """Proving the split is real rather than a blanket denial."""
        resp = config_only_client.get('/admin/htmx/configuration')
        assert resp.status_code == 200
        assert b'Notifications' in resp.data

    @pytest.mark.parametrize('path', [PAGE, LOG])
    def test_system_admin_gets_in(self, auth_client, path):
        assert auth_client.get(path).status_code == 200


class TestThePage:

    def test_it_renders(self, auth_client):
        resp = auth_client.get(PAGE)
        assert b'Notifications' in resp.data

    def test_it_loads_the_log_fragment(self, auth_client):
        assert LOG.encode() in auth_client.get(PAGE).data

    def test_it_carries_the_modal_shell_its_fragment_targets(self, auth_client):
        """The fragment's detail button swaps into #auditDetailsModalBody;
        the host page must ship it."""
        assert b'auditDetailsModalBody' in auth_client.get(PAGE).data

    def test_a_redirecting_deployment_is_announced(self, auth_client,
                                                   monkeypatch, app):
        monkeypatch.setitem(app.config, 'NOTIFY_REDIRECT_TO', 'sink@example.edu')
        resp = auth_client.get(PAGE)
        assert b'sink@example.edu' in resp.data


class TestTheLogFragment:

    def test_it_renders_the_facet_strip(self, auth_client):
        resp = auth_client.get(LOG)
        html = resp.data.decode()
        assert 'facet-grid' in html
        for label in ('Status', 'Kind'):
            assert label in html

    def test_every_declared_status_gets_a_chip_even_at_zero(self, auth_client):
        """An absent bucket reads as "not measured" rather than "none", and
        the strip is something an operator scans by position."""
        from sam.notify import NOTIFICATION_STATUSES
        html = auth_client.get(LOG).data.decode()
        for status in NOTIFICATION_STATUSES:
            assert f'data-value="{status}"' in html or status in html

    def test_an_empty_result_says_so_rather_than_rendering_a_bare_table(
            self, auth_client):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-recipient')
        assert b'No delivery attempts match' in resp.data

    def test_the_headline_count_is_the_filtered_total(self, auth_client):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-recipient')
        assert b'Showing' in resp.data
        assert b'0' in resp.data

    @pytest.mark.parametrize('query', [
        'status=sent', 'status=sent&status=failed', 'kind=expiration',
        'channel=email', 'days=1', 'days=365', 'page=2',
        'search=SCSG0001', 'days=99999', 'page=0', 'days=-5',
    ])
    def test_filter_combinations_do_not_500(self, auth_client, query):
        """Including the out-of-range ones: `days` clamps and `page` floors,
        so a hand-typed query string cannot produce a negative OFFSET."""
        assert auth_client.get(f'{LOG}?{query}').status_code == 200

    def test_an_unparseable_page_is_treated_as_the_first(self, auth_client):
        assert auth_client.get(f'{LOG}?page=notanumber').status_code == 200


class TestTheDetailModal:

    def test_a_missing_row_answers_in_the_modal_not_with_an_error_page(
            self, auth_client):
        """200, not 404: htmx does not swap a 4xx, so the already-open modal
        would keep showing the previous row."""
        resp = auth_client.get('/admin/htmx/notifications/999999999')
        assert resp.status_code == 200
        assert b'not found' in resp.data.lower()


class TestNoCliEquivalent:
    """Explicitly out of scope, and a deliberate divergence from the XRAS
    precedent where `sam-admin xras` and the web page share a query layer so
    the two cannot drift. The query layer is still shared — the door stays
    open — but nothing on the CLI consumes it yet."""

    def test_sam_admin_has_no_notifications_command(self):
        from click.testing import CliRunner
        from cli.cmds.admin import cli

        result = CliRunner().invoke(cli, ['--help'])
        assert 'notifications' not in result.output
