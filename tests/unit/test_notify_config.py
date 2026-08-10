"""NotifyConfig — the Flask-config-or-env seam, under both contexts.

The seam matters because `sam.notify` has two consumers with different config
mechanisms: `sam-admin` (environment only, no Flask) and the webapp (Flask
config, which inherits SAMConfig). A config object that worked under one and
silently defaulted under the other would make "notifications are off" mean
different things in the two halves of the same feature.
"""

import pytest

from sam.notify import NotifyConfig


class TestEnvironmentSeam:
    """Reading from os.environ, i.e. the CLI."""

    def test_defaults_are_fail_closed(self, monkeypatch):
        monkeypatch.delenv('NOTIFY_ENABLED', raising=False)
        cfg = NotifyConfig.from_environment()
        assert cfg.enabled is False
        assert cfg.transport == 'smtp'
        assert cfg.redirect_to == ''
        assert cfg.bcc == ''

    @pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'yes', 'on'])
    def test_enabled_accepts_the_usual_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv('NOTIFY_ENABLED', value)
        assert NotifyConfig.from_environment().enabled is True

    @pytest.mark.parametrize('value', ['0', 'false', 'no', '', 'maybe'])
    def test_anything_else_is_off(self, monkeypatch, value):
        monkeypatch.setenv('NOTIFY_ENABLED', value)
        assert NotifyConfig.from_environment().enabled is False

    def test_tls_defaults_on(self, monkeypatch):
        """§ 9 measured STARTTLS working on ndir; src/config.py agrees."""
        monkeypatch.delenv('MAIL_USE_TLS', raising=False)
        assert NotifyConfig.from_environment().mail_use_tls is True

    def test_int_fields_fall_back_rather_than_raise(self, monkeypatch):
        monkeypatch.setenv('MAIL_TIMEOUT', 'not-a-number')
        monkeypatch.setenv('NOTIFY_QUEUED_STALE_SECONDS', '')
        cfg = NotifyConfig.from_environment()
        assert cfg.mail_timeout == 10
        assert cfg.queued_stale_seconds == 300

    def test_values_are_read_through(self, monkeypatch):
        monkeypatch.setenv('NOTIFY_ENABLED', '1')
        monkeypatch.setenv('NOTIFY_TRANSPORT', 'console')
        monkeypatch.setenv('MAIL_SERVER', 'relay.example.edu')
        monkeypatch.setenv('MAIL_PORT', '2525')
        monkeypatch.setenv('MAIL_DEFAULT_FROM', 'noreply@example.edu')
        cfg = NotifyConfig.from_environment()
        assert (cfg.enabled, cfg.transport) == (True, 'console')
        assert (cfg.mail_server, cfg.mail_port) == ('relay.example.edu', 2525)
        assert cfg.mail_from == 'noreply@example.edu'


class TestFlaskSeam:
    """Reading from app.config, i.e. the webapp."""

    def test_app_config_wins_inside_an_app_context(self, app, monkeypatch):
        # setitem, not assignment: the `app` fixture is session-scoped, so a
        # bare write here leaks into every later test in the worker.
        monkeypatch.setenv('NOTIFY_TRANSPORT', 'smtp')
        monkeypatch.setitem(app.config, 'NOTIFY_TRANSPORT', 'console')
        monkeypatch.setitem(app.config, 'NOTIFY_ENABLED', True)
        with app.app_context():
            cfg = NotifyConfig.from_environment()
        assert cfg.transport == 'console'
        assert cfg.enabled is True

    def test_env_still_read_for_keys_absent_from_app_config(self, app, monkeypatch):
        monkeypatch.setenv('NOTIFY_REDIRECT_TO', 'staging@example.edu')
        monkeypatch.delitem(app.config, 'NOTIFY_REDIRECT_TO', raising=False)
        with app.app_context():
            cfg = NotifyConfig.from_environment()
        assert cfg.redirect_to == 'staging@example.edu'

    def test_testing_config_pins_notifications_off(self, app):
        """A test tier that CAN reach a relay eventually does."""
        assert app.config['NOTIFY_ENABLED'] is False
        assert app.config['NOTIFY_TRANSPORT'] == 'null'


class TestDerivedValues:

    def test_bcc_splits_on_commas_and_strips(self, monkeypatch):
        monkeypatch.setenv('NOTIFY_BCC', ' a@x.edu , b@x.edu ,, ')
        assert NotifyConfig.from_environment().bcc_addresses == \
            ['a@x.edu', 'b@x.edu']

    def test_no_bcc_is_an_empty_list_not_a_blank_address(self, monkeypatch):
        monkeypatch.delenv('NOTIFY_BCC', raising=False)
        assert NotifyConfig.from_environment().bcc_addresses == []

    def test_resolve_recipient_is_identity_without_a_redirect(self):
        cfg = NotifyConfig()
        assert cfg.resolve_recipient('pi@x.edu') == ('pi@x.edu', None)

    def test_resolve_recipient_rewrites_and_reports_the_original(self):
        cfg = NotifyConfig(redirect_to='me@x.edu')
        assert cfg.resolve_recipient('pi@x.edu') == ('me@x.edu', 'pi@x.edu')

    def test_a_message_already_addressed_to_the_target_is_not_redirected(self):
        """Otherwise intended_recipient would claim a redirect that never
        happened, and the ledger would show a redirect loop."""
        cfg = NotifyConfig(redirect_to='me@x.edu')
        assert cfg.resolve_recipient('me@x.edu') == ('me@x.edu', None)

    def test_summary_never_carries_the_password(self):
        cfg = NotifyConfig(mail_username='u', mail_password='hunter2')
        summary = NotifyConfig.summary(cfg)
        assert 'hunter2' not in repr(summary)
        assert 'password' not in summary
