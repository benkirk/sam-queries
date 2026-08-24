"""The Notifications tile on Admin -> Configuration.

Two things it must get right, and one it must never do.

Must: report the fail-closed state plainly (a disabled mailer that looks
enabled is worse than no card at all), and surface a redirect loudly.

Must never: name a recipient. The tile is `VIEW_SYSTEM_CONFIG`; every row of
the activity log behind "Details »" names a real person and is `SYSTEM_ADMIN`.
"""

from datetime import timedelta

import pytest
from factories.notify import make_notification_log

CONFIG_URL = '/admin/htmx/configuration'


class TestTheTileRenders:

    def test_the_card_is_present(self, auth_client):
        resp = auth_client.get(CONFIG_URL)
        assert resp.status_code == 200
        assert b'Notifications' in resp.data

    def test_it_links_to_the_activity_log(self, auth_client):
        """`auth_client` is `benkirk`, who holds SYSTEM_ADMIN — which is what the
        link is gated on. See the sibling test below for the other half."""
        resp = auth_client.get(CONFIG_URL)
        assert b'/admin/htmx/notifications' in resp.data

    def test_it_states_the_fail_closed_default(self, auth_client, app):
        """A disabled mailer that looks enabled is worse than no card."""
        assert app.config['NOTIFY_ENABLED'] is False
        resp = auth_client.get(CONFIG_URL)
        html = resp.data.decode()
        section = html[html.index('Notifications'):]
        assert 'Enabled' in section[:2000]

    def test_the_relay_is_shown(self, auth_client):
        resp = auth_client.get(CONFIG_URL)
        assert b'Relay' in resp.data


class TestSecrets:

    def test_the_mail_password_never_reaches_the_page(self, auth_client,
                                                       monkeypatch, app):
        """`NotifyConfig.summary()` has no password key at all, so this holds
        by construction rather than by the template remembering."""
        monkeypatch.setitem(app.config, 'MAIL_PASSWORD', 'hunter2-in-config')
        monkeypatch.setenv('MAIL_PASSWORD', 'hunter2-in-env')
        resp = auth_client.get(CONFIG_URL)
        assert b'hunter2' not in resp.data

    def test_the_state_block_carries_counts_and_no_rows(self, app):
        """The tile is one permission tier below the log for exactly this
        reason: it renders counts, never rows.

        Asserted on `gather_runtime_state` rather than on rendered HTML,
        because an HTML check needs a *committed* row to be meaningful — and
        committing here would escape the per-test SAVEPOINT into the shared
        xdist database. Without one it passes vacuously, which is worse than
        no test. This pins the shape instead: the block simply has no key
        that could hold an address, bar the two deliberate config fields.
        """
        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state

        with app.app_context():
            block = gather_runtime_state(app, db)['notifications']

        address_bearing = {'mail_from', 'redirect_to', 'bcc', 'addressing'}
        for key, value in block.items():
            if key in address_bearing:
                continue
            assert '@' not in str(value), \
                f'notifications.{key} carries an address: {value!r}'


class TestTheRedirectWarning:

    def test_a_redirect_is_announced(self, auth_client, monkeypatch, app):
        """A staging box quietly swallowing mail is the failure mode this
        line exists to prevent."""
        monkeypatch.setitem(app.config, 'NOTIFY_REDIRECT_TO',
                            'staging-sink@example.edu')
        resp = auth_client.get(CONFIG_URL)
        assert b'staging-sink@example.edu' in resp.data
        assert b'Redirecting to' in resp.data

    def test_no_redirect_line_when_not_redirecting(self, auth_client,
                                                   monkeypatch, app):
        monkeypatch.setitem(app.config, 'NOTIFY_REDIRECT_TO', '')
        resp = auth_client.get(CONFIG_URL)
        assert b'Redirecting to' not in resp.data


class TestPermissions:

    def test_a_viewer_without_config_permission_is_refused(self, client):
        resp = client.get(CONFIG_URL)
        assert resp.status_code in (302, 401, 403)

    def test_the_details_link_is_hidden_without_system_admin(self, auth_client,
                                                             monkeypatch):
        """The card is VIEW_SYSTEM_CONFIG; the log behind it is SYSTEM_ADMIN.

        Offering every operator a link that 403s is a small thing that reads as a
        broken page. The two tiers exist because each row of that log names a real
        person's email address, so this is the visible edge of a privacy boundary
        rather than a cosmetic gate.
        """
        from webapp.utils import rbac

        real = rbac.has_permission

        def _no_system_admin(user, permission):
            if permission is rbac.Permission.SYSTEM_ADMIN:
                return False
            return real(user, permission)

        # The context processor resolves `has_permission` from this module's globals
        # at call time, so patching the attribute reaches the template.
        monkeypatch.setattr(rbac, 'has_permission', _no_system_admin)

        resp = auth_client.get(CONFIG_URL)
        assert resp.status_code == 200
        assert b'Notifications' in resp.data          # the tile itself still renders
        assert b'/admin/htmx/notifications' not in resp.data


class TestUnavailableTable:

    def test_a_missing_table_degrades_rather_than_500s(self, auth_client,
                                                       monkeypatch):
        """`notification_log` awaits a DBA in production, so the card has to
        survive its absence — a config page that 500s is worse than one that
        says "unavailable"."""
        from webapp.utils import config_inspect

        import sam.queries.notifications as queries

        def _boom(*args, **kwargs):
            raise RuntimeError("Table 'sam.notification_log' doesn't exist")

        monkeypatch.setattr(queries, 'summarize_notifications', _boom)
        resp = auth_client.get(CONFIG_URL)
        assert resp.status_code == 200
        assert b'unavailable' in resp.data.lower()
