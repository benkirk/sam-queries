"""The shared email preview pane, rendered from a hand-built DeliveryPreview."""

from datetime import datetime

import pytest

from sam.notify import (
    DeliveryPreview, Message, PreviewRecipient, Recipient, RenderedMessage,
)

PANE = 'dashboards/fragments/email_preview_pane.html'

EMAIL_HTML = ('<!DOCTYPE html><html><head><style>body{font-family:Georgia}'
              '</style></head><body><p>Dear A PI,</p></body></html>')

LEAD = PreviewRecipient('pi@example.edu', 'A PI', 'lead')
ADMIN = PreviewRecipient('admin@example.edu', 'An Admin', 'admin',
                         last_sent=datetime(2026, 9, 1, 14, 30))


def _preview(*, mode='live', html=EMAIL_HTML, recipients=(LEAD,), selected=0,
             intended=None, to=None, bcc=(), transport='smtp', error=None):
    who = recipients[selected] if recipients else None
    outgoing = Message(kind='xras_activation', subject='Now active',
                       recipient=Recipient(to or (who.address if who else 'x@x')),
                       intended_recipient=intended)
    rendered = None if error else RenderedMessage(
        subject='Now active', text='plain text part.', html=html,
        template_text='xras_activation.txt',
        template_html='xras_activation.html' if html else None)
    return DeliveryPreview(mode=mode, transport=transport,
                           recipients=tuple(recipients), selected=who,
                           outgoing=outgoing if who else None,
                           sender='sam-admin@ucar.edu', cc=(), bcc=tuple(bcc),
                           rendered=rendered if who else None, error=error)


@pytest.fixture
def render(app):
    from flask import render_template

    def _render(p, **kw):
        kw.setdefault('picker_url', '/preview/7')
        with app.test_request_context():
            return render_template(PANE, p=p, id_prefix='tstPreview',
                                   pane_id='tstPane', picker_method='get',
                                   picker_include=None, notes=kw.pop('notes', []),
                                   empty=None, **kw)
    return _render


class TestTheFrame:

    def test_the_html_is_in_a_bare_sandbox(self, render):
        iframe = render(_preview()).split('<iframe')[1].split('>')[0]
        assert ' sandbox' in iframe
        assert 'allow-' not in iframe

    def test_the_emails_css_never_reaches_the_host_document(self, render):
        body = render(_preview())
        assert '<style>' not in body
        assert 'font-family:Georgia' in body

    def test_it_emits_no_form_and_no_modal_toggle(self, render):
        body = render(_preview(recipients=(LEAD, ADMIN)))
        assert '<form' not in body
        assert 'data-bs-toggle="modal"' not in body


class TestTabs:

    def test_tab_ids_follow_the_prefix(self, render):
        body = render(_preview())
        for suffix in ('HtmlTab', 'Html', 'TextTab', 'Text'):
            assert f'id="tstPreview{suffix}"' in body
        assert 'plain text part.' in body

    def test_no_html_means_no_tabs(self, render):
        body = render(_preview(html=None))
        assert 'nav-tabs' not in body and 'srcdoc' not in body
        assert 'plain text part.' in body


class TestThePicker:

    def test_one_recipient_has_no_picker(self, render):
        assert 'preview_recipient' not in render(_preview())

    def test_two_recipients_get_a_picker_aimed_at_the_pane(self, render):
        body = render(_preview(recipients=(LEAD, ADMIN), selected=1))
        select = body.split('<select')[1].split('>')[0]
        assert 'name="preview_recipient"' in select
        assert 'hx-get="/preview/7"' in select
        assert 'hx-target="#tstPane"' in select
        assert '<option value="admin@example.edu" selected' in body


class TestBanners:

    def test_disabled(self, render):
        assert 'not enabled' in render(_preview(mode='disabled'))

    def test_redirected_names_both_addresses(self, render):
        body = render(_preview(mode='redirected', to='me@example.edu',
                               intended='pi@example.edu'))
        assert 'redirected' in body and 'me@example.edu' in body
        assert '(for pi@example.edu)' in body

    def test_live_smtp_has_no_banner(self, render):
        body = render(_preview())
        assert 'alert-warning' not in body and 'transport is configured' not in body

    def test_a_console_transport_says_nothing_is_delivered(self, render):
        assert 'server log' in render(_preview(transport='console'))

    def test_the_host_can_turn_the_mode_banner_off(self, render):
        assert 'not enabled' not in render(_preview(mode='disabled'),
                                           mode_banner=False)


class TestEnvelope:

    def test_bcc_is_listed(self, render):
        assert 'audit@example.edu' in render(_preview(bcc=('audit@example.edu',)))

    def test_last_sent_is_shown_for_the_selected_recipient(self, render):
        body = render(_preview(recipients=(LEAD, ADMIN), selected=1))
        assert 'Already sent to <code>admin@example.edu</code>' in body
        assert '2026-09-01 14:30' in body
        assert 'Already sent' not in render(_preview(recipients=(LEAD, ADMIN)))

    def test_a_render_error_is_shown(self, render):
        body = render(_preview(error='unexpected end of template'))
        assert 'could not be rendered' in body
        assert 'unexpected end of template' in body

    def test_notes_render(self, render):
        assert 'Link created on send' in render(_preview(),
                                                notes=['Link created on send'])

    def test_an_empty_preview_says_so(self, render):
        body = render(_preview(recipients=()))
        assert 'Nothing to preview' in body and 'stat-item' not in body


def test_an_info_pane(app):
    from webapp.utils.email_preview import render_preview_info
    with app.test_request_context():
        body = render_preview_info('Type a reason to preview.', 'warning')
    assert 'alert-warning' in body and 'Type a reason to preview.' in body
