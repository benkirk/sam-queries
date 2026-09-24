"""`webapp.utils.notify.get_notifier`: always session-backed, and read-only on request."""

from sam.notify import Message, NotifyConfig, NullTransport, Recipient


def _message():
    return Message(kind='xras_extension', subject='s',
                   recipient=Recipient('pi@example.edu'), dedup_key='k')


class TestGetNotifier:

    def test_overrides_and_addressing_rows_load_through_the_ledger(self, app):
        """The regression: a preview notifier used to have no ledger, so it
        skipped operator template overrides and addressing rows."""
        from webapp.utils.notify import get_notifier
        with app.test_request_context():
            for read_only in (False, True):
                notifier = get_notifier(read_only=read_only)
                assert notifier.ledger.read_only is read_only
                factory = notifier.ledger.session_factory
                assert notifier.renderer.loader.session_factory is factory
                assert notifier.addressing_store.session_factory is factory

    def test_a_read_only_notifier_cannot_send(self, app):
        from webapp.utils.notify import get_notifier
        with app.test_request_context():
            notifier = get_notifier(read_only=True)
            notifier.config = NotifyConfig(enabled=True, transport='null')
            notifier._transport = transport = NullTransport()
            result = notifier.send(_message())
        assert result.status == 'failed'
        assert 'refusing to send' in result.detail
        assert transport.delivered == []

    def test_notify_config_reads_app_config(self, app, monkeypatch):
        from webapp.utils.notify import notify_config
        monkeypatch.setitem(app.config, 'NOTIFY_REDIRECT_TO', 'me@example.edu')
        with app.app_context():
            assert notify_config().redirect_to == 'me@example.edu'
