"""The XRAS Notify button, now that it sends mail.

Auth, 404s and render smoke, per the house convention that happy-path writes
are covered at the model layer — route handlers use Flask-SQLAlchemy's
`db.session`, so a factory row in the test session is invisible to them and a
route-level write would COMMIT into the shared xdist database.

What is *not* left to the model layer is the **decision** this route makes:
whether to write a `notified` activation event at all. That is route logic,
and getting it wrong leaves the card claiming a handoff nobody received. It
is tested by spying on `_record_activation_event` rather than by inspecting
committed rows.
"""

from contextlib import contextmanager

import pytest

from sam.notify import (
    DeliveryResult, Message, Notifier, NotifyConfig, NullTransport, Recipient,
    TransportError,
)
from webapp.utils.rbac import Permission

#: A snapshot project whose lead has an email address and which owns
#: allocations — the shape the notify path needs to build a message at all.
PROJECT_ID = 1


@pytest.fixture
def transport():
    return NullTransport()


@pytest.fixture
def notifier(monkeypatch, transport):
    """Swap the route's notifier for one that records and never sends.

    `ledger=None` keeps the route off the database entirely: the ledger
    commits by design, which would escape the per-test SAVEPOINT.
    """
    from webapp.dashboards.allocations import blueprint

    built = Notifier(config=NotifyConfig(enabled=True), transport=transport,
                     ledger=None)
    monkeypatch.setattr(blueprint, 'get_notifier', lambda **kw: built)
    return built


@pytest.fixture
def events(monkeypatch):
    """Spy on the activation-event write, without performing it."""
    from webapp.dashboards.allocations import blueprint

    recorded = []

    class _Event:
        creation_time = None

    def _spy(project, event_type, *, comment=None, notified_to=None,
             action_log_id=None):
        recorded.append({'project': project, 'event_type': event_type,
                         'notified_to': notified_to,
                         'action_log_id': action_log_id})
        return _Event()

    monkeypatch.setattr(blueprint, '_record_activation_event', _spy)
    return recorded


def _results(*statuses, address='pi@example.edu'):
    """Hand-built DeliveryResults, for driving the route's branching."""
    out = []
    for i, status in enumerate(statuses):
        message = Message(kind='xras_activation', subject='s',
                          recipient=Recipient(f'{i}-{address}', name='A PI',
                                              role='lead'))
        out.append(DeliveryResult(
            ok=status in ('sent', 'redirected', 'suppressed'),
            status=status, message=message,
            detail='550 refused' if status == 'failed' else None))
    return out


@pytest.fixture
def send_result(monkeypatch, notifier):
    """Force `send_many` to return a chosen set of outcomes."""
    def _set(*statuses):
        monkeypatch.setattr(notifier, 'send_many',
                            lambda messages, **kw: _results(*statuses))
    return _set


# ── The preview modal ────────────────────────────────────────────────────────

class TestPreviewForm:

    def test_it_renders_the_recipients(self, auth_client, notifier):
        resp = auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        assert resp.status_code == 200
        assert b'Recipients' in resp.data

    def test_it_renders_the_message_body(self, auth_client, notifier):
        """The point of a preview over an hx-confirm: it answers 'and what
        does it say', not just 'are you sure'."""
        resp = auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        html = resp.data.decode()
        assert 'is now active' in html

    def test_it_offers_a_send_button_pointing_at_the_post_route(
            self, auth_client, notifier):
        resp = auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        assert f'/allocations/xras_notify/{PROJECT_ID}'.encode() in resp.data

    def test_the_preview_sends_nothing(self, auth_client, notifier, transport):
        auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        assert transport.delivered == []

    def test_a_missing_project_answers_in_the_modal_not_with_an_error_page(
            self, auth_client, notifier):
        """200, not 404, and deliberately so: htmx does not swap a 4xx by
        default, so a 404 would leave the already-open modal showing a
        spinner or the previous project's content. See
        `webapp.utils.htmx.htmx_modal_not_found`."""
        resp = auth_client.get('/allocations/xras_notify_form/999999999')
        assert resp.status_code == 200
        assert b'not found' in resp.data.lower()

    def test_a_disabled_deployment_says_so_before_the_operator_clicks_send(
            self, auth_client, monkeypatch, transport):
        from webapp.dashboards.allocations import blueprint
        built = Notifier(config=NotifyConfig(enabled=False),
                         transport=transport, ledger=None)
        monkeypatch.setattr(blueprint, 'get_notifier', lambda **kw: built)

        resp = auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        assert b'not enabled' in resp.data

    def test_a_redirecting_deployment_names_the_redirect_target(
            self, auth_client, monkeypatch, transport):
        """A staging box quietly swallowing mail is the failure mode this
        line exists to prevent."""
        from webapp.dashboards.allocations import blueprint
        built = Notifier(config=NotifyConfig(enabled=True,
                                             redirect_to='staging@example.edu'),
                         transport=transport, ledger=None)
        monkeypatch.setattr(blueprint, 'get_notifier', lambda **kw: built)

        resp = auth_client.get(f'/allocations/xras_notify_form/{PROJECT_ID}')
        assert b'staging@example.edu' in resp.data
        assert b'redirected' in resp.data.lower()


class TestTheNoticeMayContradictTheProject:
    """Nothing orders Notify after Activate.

    Rendered directly rather than driven over HTTP: the flag turns on a
    project's `active` column, and a route-level test would need a committed
    write on `db.session`'s own connection to move it. The template is what
    is actually fragile here.

    Measured in the pre-deploy smoke: an activation notice reading "is now
    active" left the building at 15:11:53 for a project that was not
    activated until 15:12:57.
    """

    TEMPLATE = 'dashboards/allocations/partials/xras_notify_form.html'

    def _render(self, app, *, project_inactive):
        from types import SimpleNamespace
        from flask import render_template
        with app.test_request_context():
            return render_template(
                self.TEMPLATE,
                project=SimpleNamespace(projcode='UHSS0004', project_id=7),
                people=[SimpleNamespace(name='A PI', email='pi@example.edu',
                                        role='lead')],
                preview=SimpleNamespace(subject='… is now active',
                                        body='Your project … is now active',
                                        template='xras_activation.txt'),
                preview_error=None,
                already_notified=[],
                notify_enabled=True,
                redirect_to=None,
                project_inactive=project_inactive,
                post_url='/allocations/xras_notify/7')

    def test_an_inactive_project_is_called_out_before_the_operator_sends(
            self, app):
        body = self._render(app, project_inactive=True)
        assert 'UHSS0004 is not active yet' in body
        assert 'Activate the project first' in body

    def test_an_active_project_gets_no_such_warning(self, app):
        body = self._render(app, project_inactive=False)
        assert 'not active yet' not in body

    def test_the_warning_does_not_disable_sending(self, app):
        """Deliberate: the operator may be about to activate, and a hard block
        would make a legitimate order of work impossible."""
        body = self._render(app, project_inactive=True)
        assert '/allocations/xras_notify/7' in body
        assert 'disabled' not in body.split('hx-post')[1][:400]


class TestThePreviewShowsBothParts:
    """The modal used to show only the text part.

    Most recipients see the HTML one, and until this existed it was reviewed by
    reading the file — during the pre-deploy smoke its templates were edited
    twice that way. Both parts come out of a single `render()` call
    (`RenderedMessage`), so nothing had to be fetched; they were being dropped.
    """

    TEMPLATE = 'dashboards/allocations/partials/xras_notify_form.html'

    #: A whole document with a <style> block, like the real templates.
    EMAIL_HTML = ('<!DOCTYPE html><html><head><style>'
                  'body{font-family:Georgia}.footer{color:#666}'
                  '</style></head><body><p>Dear A PI,</p></body></html>')

    def _render(self, app, *, html=EMAIL_HTML):
        from types import SimpleNamespace
        from flask import render_template
        with app.test_request_context():
            return render_template(
                self.TEMPLATE,
                project=SimpleNamespace(projcode='UHSS0004', project_id=7),
                people=[SimpleNamespace(name='A PI', email='pi@example.edu',
                                        role='lead')],
                preview=SimpleNamespace(
                    subject='s', text='Dear A PI,\n\nplain text part.',
                    html=html, template_text='xras_activation.txt',
                    template_html='xras_activation.html' if html else None),
                preview_error=None, already_notified=[], notify_enabled=True,
                redirect_to=None, project_inactive=False,
                post_url='/allocations/xras_notify/7')

    def test_both_parts_are_offered(self, app):
        body = self._render(app)
        assert 'id="xrasPreviewHtml"' in body
        assert 'id="xrasPreviewText"' in body
        assert 'plain text part.' in body

    def test_html_is_the_default_tab(self, app):
        """It is what most recipients see, so it is what an operator should be
        looking at when they decide whether to send."""
        body = self._render(app)
        html_tab = body.split('id="xrasPreviewHtmlTab"')[1].split('>')[0]
        text_tab = body.split('id="xrasPreviewTextTab"')[1].split('>')[0]
        assert 'aria-selected="true"' in html_tab
        assert 'aria-selected="false"' in text_tab

    def test_the_html_is_sandboxed(self, app):
        """No scripts, no same-origin. A preview is not a browser."""
        body = self._render(app)
        iframe = body.split('<iframe')[1].split('>')[0]
        assert 'sandbox' in iframe
        assert 'allow-scripts' not in iframe
        assert 'allow-same-origin' not in iframe

    def test_the_emails_css_never_reaches_the_host_document(self, app):
        """⚠️ The regression this whole design exists to prevent.

        The email templates are whole pages whose <style> blocks use bare
        selectors (`body`, `h3`) plus `.footer`, which the dashboard also uses.
        Inlined rather than framed, they restyle the page around them. The only
        legal place for that CSS is inside the srcdoc ATTRIBUTE, where it is
        escaped text rather than markup the parser acts on.
        """
        body = self._render(app)
        # Escaped inside the attribute — never a live <style> tag.
        assert '<style>' not in body
        assert 'font-family:Georgia' in body            # present, but escaped
        assert 'srcdoc="' in body

    def test_no_html_variant_means_no_tab_strip(self, app):
        """A lone tab answers a question nobody asked; fall back to exactly the
        single <pre> this modal showed before."""
        body = self._render(app, html=None)
        assert 'nav-tabs' not in body
        assert 'srcdoc' not in body
        assert 'plain text part.' in body

    def test_both_template_names_are_named(self, app):
        """The pair is what shows the two parts resolved to the SAME facility
        variant — the failure mode the stat exists to surface."""
        body = self._render(app)
        assert 'xras_activation.txt' in body
        assert 'xras_activation.html' in body


# ── The send ─────────────────────────────────────────────────────────────────

class TestSendRecordsOnlyWhatWasDelivered:

    def test_a_successful_send_records_a_notified_event(
            self, auth_client, send_result, events):
        send_result('sent', 'sent')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert resp.status_code == 200
        assert [e['event_type'] for e in events] == ['notified']

    def test_notified_to_names_the_addresses_that_succeeded(
            self, auth_client, send_result, events):
        send_result('sent', 'failed')
        auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        notified_to = events[0]['notified_to']
        assert '0-pi@example.edu' in notified_to
        assert '1-pi@example.edu' not in notified_to, \
            'a failed delivery must not appear in the audit answer'

    def test_a_partial_send_still_records_and_names_the_failures(
            self, auth_client, send_result, events):
        send_result('sent', 'failed')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert len(events) == 1
        assert b'Partially sent' in resp.data
        assert b'550 refused' in resp.data

    def test_a_redirected_delivery_counts_as_delivered_but_is_reported_apart(
            self, auth_client, send_result, events):
        """It really was delivered — just not to its subject."""
        send_result('redirected')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert len(events) == 1
        assert b'Redirected' in resp.data


class TestNothingDeliveredWritesNoEvent:
    """The decision that matters. A `notified` event the card derives from,
    written when nothing left the building, leaves the card claiming a
    handoff nobody received."""

    def test_all_failed_writes_no_event(self, auth_client, send_result, events):
        send_result('failed', 'failed')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert resp.status_code == 200
        assert events == []

    def test_all_failed_hands_the_operator_the_addresses(
            self, auth_client, send_result, events):
        send_result('failed')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert b'nothing was sent' in resp.data.lower()
        assert b'Send it yourself' in resp.data

    def test_notify_disabled_writes_no_event(self, auth_client, monkeypatch,
                                             events, transport):
        from webapp.dashboards.allocations import blueprint
        built = Notifier(config=NotifyConfig(enabled=False),
                         transport=transport, ledger=None)
        monkeypatch.setattr(blueprint, 'get_notifier', lambda **kw: built)

        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert resp.status_code == 200
        assert events == []
        assert transport.delivered == []

    def test_everyone_already_notified_writes_no_event(
            self, auth_client, send_result, events):
        """Suppression counts as "nothing delivered" here on purpose: there
        is no new handoff to record, and a second `notified` event would be
        the double-count the derive rule exists to prevent."""
        send_result('suppressed', 'suppressed')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert events == []
        assert b'already been notified' in resp.data

    def test_the_card_is_told_to_refresh_either_way(self, auth_client,
                                                    send_result, events):
        send_result('failed')
        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')


class TestNoPathMay500:

    def test_a_transport_failure_is_a_dialog_not_an_exception(
            self, auth_client, monkeypatch, events, transport):
        from webapp.dashboards.allocations import blueprint

        class Unreachable(NullTransport):
            def open(self):
                raise TransportError('connection refused')

        built = Notifier(config=NotifyConfig(enabled=True),
                         transport=Unreachable(), ledger=None)
        monkeypatch.setattr(blueprint, 'get_notifier', lambda **kw: built)

        resp = auth_client.post(f'/allocations/xras_notify/{PROJECT_ID}')
        assert resp.status_code == 200
        assert events == []
        assert b'connection refused' in resp.data

    def test_a_missing_project_is_a_message_not_an_error_page(
            self, auth_client, notifier):
        resp = auth_client.post('/allocations/xras_notify/999999999')
        assert resp.status_code == 404
        assert b'not found' in resp.data.lower()


# ── The card ─────────────────────────────────────────────────────────────────

class TestTheButtonIsNowTwoSteps:

    def test_the_card_opens_the_preview_rather_than_posting(self, auth_client):
        resp = auth_client.get('/allocations/xras_pending_fragment')
        html = resp.data.decode()
        if 'xras_notify' not in html:
            pytest.skip('no pending XRAS projects in this snapshot')
        assert 'xras_notify_form' in html, \
            'the Notify button must GET the preview, not POST an irreversible send'
