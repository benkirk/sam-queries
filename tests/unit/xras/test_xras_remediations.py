"""The XRAS Remediations card and its modal routes.

House convention for the HTTP tier: auth, validation, 404s and render smoke.
The write happy-paths are covered at the service layer
(``test_xras_remediation_service.py``), where they belong — a route test that
exercised a real write would either mock the whole world or touch production
XRAS.

Two properties here are specific to this card and worth stating:

* it is a **card, not a fourth tab** — three panes stay three;
* every modal GET degrades with a **200**, because htmx will not swap a 4xx
  into an already-open modal, so an error status renders as an empty modal
  indistinguishable from a broken button.
"""

from __future__ import annotations

import re

from _paths import REPO_ROOT
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.base import XrasSourceUnavailable
from sam.queries.xras_requests import request_index_entry

# The shared fixtures and payload builders; `_cache` is the autouse cache reset.
from _remediations_support import (  # noqa: F401
    _cache,
    FRAGMENT,
    view_only_client,
    configured,
    armed,
    _payload,
    _posted,
    _publish,
    _detail_payload,
    _reader,
    _publish_with_verdict,
)

# Most tests below publish UNCHECKED rows (no preflight), which the default
# pending-work queue excludes by definition, so they read the card with
# show_all=1. TestPendingWorkToggle covers the default.


# access control

class TestAccessControl:

    def test_anonymous_is_redirected(self, client):
        assert client.get(FRAGMENT).status_code in (302, 401)

    def test_view_only_is_forbidden(self, view_only_client):
        """VIEW_XRAS is not enough — every control here writes to XRAS."""
        assert view_only_client.get(FRAGMENT).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge_form/ghost-user-abcde',
        '/allocations/xras_withdraw_form/EXAM0001/7',
        '/allocations/xras_resubmit_form/EXAM0001/7',
        '/allocations/xras_request_detail/EXAM0001',
        '/allocations/xras_readiness_detail/EXAM0001',
        '/allocations/xras_user_detail/janebaldwin',
        '/allocations/xras_opportunity_detail/532220',
        '/allocations/xras_resource_form/EXAM0001/7/530201',
        '/allocations/xras_dates_form/EXAM0001/7',
        '/allocations/xras_attributes_form/EXAM0001',
        '/allocations/xras_action_fields_form/EXAM0001/7',
    ])
    def test_every_modal_is_gated(self, view_only_client, path):
        assert view_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge/ghost-user-abcde',
        '/allocations/xras_withdraw/EXAM0001/7',
        '/allocations/xras_resubmit/EXAM0001/7',
        '/allocations/xras_role_add/EXAM0001',
        '/allocations/xras_role_remove/EXAM0001/2',
        '/allocations/xras_resource_edit/EXAM0001/7/530201',
        '/allocations/xras_resource_remove/EXAM0001/7/530201',
        '/allocations/xras_dates_edit/EXAM0001/7',
        '/allocations/xras_dates_remove/EXAM0001/7/9',
        '/allocations/xras_attributes_edit/EXAM0001',
        '/allocations/xras_action_fields_edit/EXAM0001/7',
        '/allocations/xras_recheck_request/EXAM0001',
        '/allocations/xras_recheck_visible',
        # xras_set_override / xras_clear_override are ADMIN_XRAS, not MANAGE — see
        # TestOverridesRequireAdmin. view_only_client keeps ADMIN, so it is NOT
        # blocked from them and cannot assert 403 here.
    ])
    def test_every_write_is_gated(self, view_only_client, path):
        assert view_only_client.post(path).status_code == 403

    def test_the_mnemonic_code_typeahead_returns_rows(self, auth_client):
        resp = auth_client.get('/allocations/htmx/search-mnemonic-codes?q=a')
        assert resp.status_code == 200
        assert b'fk-search-result' in resp.data or b'No matching' in resp.data

    def test_the_page_shell_hides_the_card_from_a_view_only_operator(
            self, view_only_client):
        """Neither the container nor its facet form may render."""
        body = view_only_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' not in body
        assert 'xras-remediation-filters' not in body

    def test_the_page_shell_carries_both_for_an_admin(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' in body
        assert 'xras-remediation-filters' in body


class TestRecheckNow:
    """The request-scoped, never-writes re-check that patches the snapshot."""

    def test_it_patches_the_snapshot_with_fresh_verdicts(self, auth_client,
                                                          configured, monkeypatch):
        _publish(_payload('EXAM0001'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001')
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        entry = next(r for r in xras_cache.load_requests_index()['rows']
                     if r['request_number'] == 'EXAM0001')
        # A re-check writes nothing, so it must NOT stamp the "updated since the
        # sweep" marker — the flipped verdict is the only tell.
        assert entry['refreshed_at'] is None
        assert entry['actions'][0]['preflight'] is not None

    def test_it_degrades_200_when_xras_is_unreachable(self, auth_client,
                                                      configured, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001')
        # 200, not 4xx: htmx will not swap a 4xx into the open modal.
        assert resp.status_code == 200
        assert 'could not be reached' in resp.get_data(as_text=True)

    def test_from_the_dialog_it_rerenders_the_dialog(self, auth_client, configured,
                                                      monkeypatch):
        _publish(_payload('EXAM0001'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001?in_dialog=1')
        body = resp.get_data(as_text=True)
        assert 'xras-flash' in body and 'Re-checked EXAM0001' in body
        assert 'SAM pre-flight' in body                 # the dialog, not a bare toast
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        # The fragment swaps into the open modal: it must not toggle that modal (#464).
        assert 'data-bs-target="#xrasModal"' not in body

    def test_from_the_dialog_an_outage_keeps_the_dialog(self, auth_client, configured,
                                                        monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_family_by_number.side_effect = XrasSourceUnavailable('down')
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001?in_dialog=1')
        assert resp.headers.get('HX-Reswap') == 'none'
        assert 'could not be reached' in resp.get_data(as_text=True)

    def test_the_modal_renders_the_preflight_section_from_the_snapshot(
            self, auth_client, configured, monkeypatch):
        payload = _detail_payload('EXAM0001')
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': ['PI ghost is not in database'], 'gaps': [],
                   'service': 'supplement', 'stage': 'Approved',
                   'action_status': 'Approved', 'request_status': 'Approved',
                   'push_state': 'pending', 'push_detail': None, 'resolved': None,
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(),
            'statuses': ['Approved'], 'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'SAM pre-flight' in body
        assert 'PI ghost is not in database' in body


def _publish_one_verdict(status, *, messages=(), service='supplement'):
    """Publish EXAM0001 carrying a single-action verdict of the given status."""
    payload = _detail_payload('EXAM0001')
    verdict = {'status': status, 'would_succeed': status == 'rechecked',
               'messages': list(messages), 'gaps': [], 'service': service,
               'stage': 'Approved', 'action_status': 'Approved',
               'request_status': 'Approved', 'push_state': 'pending',
               'push_detail': None, 'resolved': None,
               'checked_at': '2026-08-23T09:00:00'}
    xras_cache.store_requests_index({
        'generated_at': datetime.now(), 'statuses': ['Approved'],
        'extra_statuses': {},
        'rows': [request_index_entry(payload, pending_push=True,
                                     preflights={7: verdict})]})


class TestReadinessModal:
    """The focused push-readiness modal a verdict badge opens — snapshot-only."""

    def test_it_renders_the_reasons_without_a_live_read(self, auth_client):
        # No _reader() is scripted: the modal reads the swept verdict straight
        # from the snapshot, so it needs no live XRAS call to explain a badge.
        _publish_one_verdict('failed', messages=['PI ghost is not in database'])
        body = auth_client.get(
            '/allocations/xras_readiness_detail/EXAM0001').get_data(as_text=True)
        assert 'SAM pre-flight' in body
        assert 'PI ghost is not in database' in body
        assert 'checked as' in body and 'supplement' in body

    def test_an_unswept_request_is_not_found(self, auth_client):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': [], 'extra_statuses': {},
            'rows': []})
        resp = auth_client.get('/allocations/xras_readiness_detail/NOPE0001')
        # 200, not 4xx: htmx will not swap a 4xx into an already-open modal.
        assert resp.status_code == 200
        assert 'SAM pre-flight' not in resp.get_data(as_text=True)


class TestReadinessBand:
    """The dialog says what blocks the next push before anything else."""

    def _verdict(self, status, *, messages=(), gaps=()):
        payload = _detail_payload('EXAM0001')
        verdict = {'status': status, 'would_succeed': status == 'rechecked',
                   'messages': list(messages), 'gaps': list(gaps), 'service': 'new',
                   'stage': 'Requested', 'action_status': 'Submitted',
                   'request_status': 'Submitted', 'push_state': 'pending',
                   'push_detail': None, 'resolved': None,
                   'checked_at': '2026-08-23T09:00:00'}
        payload['actions'][0]['actionStatus'] = 'Submitted'
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Submitted'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})
        return payload

    def _band(self, auth_client, monkeypatch, payload):
        _reader(monkeypatch, payload=payload)
        body = auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras-readiness-band' in body
        return body.split('xras-readiness-band', 1)[1].split('</div>', 1)[0]

    def test_it_leads_with_the_first_reason(self, auth_client, configured, monkeypatch):
        band = self._band(auth_client, monkeypatch,
                          self._verdict('failed', messages=['PI ghost is not in database',
                                                            'Contract 123 not found']))
        assert 'PI ghost is not in database' in band
        assert '+1 more below' in band
        assert 'Contract 123' not in band

    def test_a_missing_date_range_offers_add_dates(self, auth_client, armed, monkeypatch):
        band = self._band(auth_client, monkeypatch,
                          self._verdict('incomplete', gaps=['no_allocation_dates']))
        assert 'no allocation dates on this action' in band
        assert 'Add dates' in band and '/xras_dates_form/' in band

    def test_a_request_in_review_waits_for_its_dates_without_offering_them(
            self, auth_client, armed, monkeypatch):
        band = self._band(auth_client, monkeypatch,
                          self._verdict('incomplete', gaps=['dates_at_approval']))
        assert 'dates arrive when XRAS approves it' in band
        assert 'Add dates' not in band

    def test_a_red_verdict_on_stand_in_dates_says_it_was_checked(
            self, auth_client, configured, monkeypatch):
        _reader(monkeypatch, payload=self._verdict(
            'failed', messages=['PI ghost is not in database'], gaps=['dates_at_approval']))
        body = auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Checked on provisional dates' in body
        assert "Can't pre-flight yet" not in body

    def test_gap_labels_read_as_words(self):
        from sam.xras.preflight import gap_label
        assert gap_label('resource_id_unmapped:530201') == 'XRAS resource 530201 has no SAM mapping'
        assert gap_label('something_new') == 'something_new'


class TestReadinessBadgeWiring:
    """Card badges: a verdict opens the modal, incomplete is passive, a not-checked
    row posts a recheck."""

    def test_a_verdict_badge_links_to_the_readiness_modal(self, auth_client,
                                                          configured):
        _publish_one_verdict('failed', messages=['x'])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras_readiness_detail/EXAM0001' in body
        # Icon-only in the dense cell; the Readiness facet chip keeps "Would fail".
        assert 'fa-triangle-exclamation' in body

    def test_an_incomplete_badge_is_passive(self, auth_client, configured):
        _publish_one_verdict('incomplete')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'fa-circle-question' in body
        # No re-check (it can't resolve it) and no modal link.
        assert 'xras_recheck_request/EXAM0001' not in body
        assert 'xras_readiness_detail/EXAM0001' not in body

    def test_a_not_checked_row_posts_the_inline_recheck(self, auth_client,
                                                        configured):
        # No preflight at all -> rollup None -> the checkable "not checked" state.
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'not checked' in body
        assert 'xras_recheck_request/EXAM0001' in body


class TestActionsColumnAndFacet:
    """The Type column (#2, between Request and Status) and its facet chip."""

    def _publish_types(self, *types):
        payloads = []
        for i, kind in enumerate(types):
            p = _payload('EXAM%04d' % i)
            p['actions'][0]['actionType'] = kind
            payloads.append(p)
        _publish(*payloads)

    def test_the_column_renders_the_type(self, auth_client, configured):
        self._publish_types('New')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '>Type<' in body                # the header
        assert 'New' in body                   # the row badge

    def test_the_facet_narrows_to_the_chosen_type(self, auth_client, configured):
        self._publish_types('New', 'Supplement')
        body = auth_client.get(
            FRAGMENT + '?show_all=1&action_type=New').get_data(as_text=True)
        assert 'EXAM0000' in body             # the New request survives
        assert 'EXAM0001' not in body         # the Supplement request is filtered

    def test_it_shows_only_the_latest_action_type(self, auth_client, configured):
        # A request with an old New and an in-flight Extension shows Extension —
        # what admin names it — not the whole history.
        p = _payload('EXAM0009')
        p['actions'] = [
            {'actionId': 1, 'actionType': 'Supplement', 'actionStatus': 'Approved'},
            {'actionId': 2, 'actionType': 'Extension', 'actionStatus': 'Submitted'}]
        _publish(p)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Extension' in body
        assert 'Supplement' not in body       # neither the cell nor the facet


class TestWaitingFacet:
    """The Waiting chips band the handoff's age; the Age cell tints past 30 and 90 days."""

    def test_the_bands_cut_at_7_30_and_90_days(self):
        from datetime import date, timedelta
        from sam.queries.xras_requests import waiting_band
        today = date(2026, 11, 5)
        ago = lambda n: today - timedelta(days=n)
        assert [waiting_band(ago(n), today) for n in (6, 7, 29, 30, 89, 90)] == [
            'week', 'month', 'month', 'quarter', 'quarter', 'older']
        assert waiting_band(datetime(2026, 11, 1, 9), today) == 'week'
        assert waiting_band(None, today) is None

    def test_the_chip_words_and_the_tint_note_come_from_the_one_table(self):
        """#920 F3: the band words lived in three places; the bounds now live in one."""
        from sam.queries.xras_requests import WAITING_BANDS, WAITING_BAND_LABELS, waiting_note
        assert list(WAITING_BAND_LABELS) == [b for b, _ in WAITING_BANDS] + ['older']
        assert waiting_note('quarter') == ': waiting over 30 days'
        assert waiting_note('older') == ': waiting over 90 days'
        assert waiting_note('week') == waiting_note('month') == waiting_note(None) == ''

    def _publish_ages(self):
        from datetime import timedelta
        stamp = lambda n: (datetime.now() - timedelta(days=n)).strftime('%Y-%m-%dT00:00:00Z')
        _publish(_payload('EXAM0001', submit_date=stamp(2)),
                 _payload('EXAM0002', submit_date=stamp(100)))

    def test_the_strip_counts_each_band(self, auth_client, configured):
        self._publish_ages()
        body = auth_client.get(FRAGMENT, query_string={
            'show_all': '1', 'start_date': '2000-01-01'}).get_data(as_text=True)
        assert 'Filter by Waiting Under a week' in body
        assert 'Filter by Waiting Over 3 months' in body
        assert 'Filter by Waiting 1–4 weeks' not in body      # hide_zero

    def test_a_chip_narrows_the_rows_and_the_batch_recheck(self, auth_client, configured,
                                                           monkeypatch):
        self._publish_ages()
        body = auth_client.get(FRAGMENT, query_string={
            'show_all': '1', 'start_date': '2000-01-01', 'waiting': 'older'}).get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body
        _reader(monkeypatch, payload=_detail_payload('EXAM0002'))
        resp = auth_client.post('/allocations/xras_recheck_visible', data={
            'show_all': '1', 'start_date': '2000-01-01', 'waiting': 'older'})
        assert 'Checked 1 request' in resp.get_data(as_text=True)

    def test_the_dialog_says_how_long_it_has_waited(self, auth_client, configured,
                                                     monkeypatch):
        payload = _detail_payload('EXAM0001')
        payload['submitDate'] = '2015-01-01T00:00:00Z'
        _publish(payload)
        _reader(monkeypatch, payload=payload)
        body = auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        meta = body[body.index('xras-request-meta'):body.index('xras-request-title')]
        assert 'waiting <span class="text-danger-emphasis' in meta

    def test_a_stale_age_is_tinted(self, auth_client, configured):
        self._publish_ages()
        body = auth_client.get(FRAGMENT, query_string={
            'show_all': '1', 'start_date': '2000-01-01'}).get_data(as_text=True)
        assert 'waiting over 90 days' in body
        assert 'text-danger-emphasis fw-semibold' in body


class TestQueueNav:
    """The dialog's prev/next walks the card's rows; the wiring is markup, the walk is xras.js."""

    def test_the_card_marks_its_rows_as_the_queue(self, auth_client, configured):
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-xras-queue>' in body
        assert 'data-request-number="EXAM0001"' in body

    def test_the_request_dialog_carries_the_hidden_nav(self, auth_client, armed, monkeypatch):
        _publish(_payload())
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        footer = body[body.index('id="xrasModalFooter"'):]
        assert 'data-xras-queue-nav data-request-number="EXAM0001" hidden' in footer
        assert 'data-action="xras-queue-step"' in footer

    def test_a_sub_dialog_does_not(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch)
        _publish(_payload())
        body = auth_client.get('/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'data-xras-queue-nav' not in body


class TestTheDocumentProxy:
    """A document, fetched as the line's PI and served from our origin; nothing stored."""

    URL = '/allocations/xras_document/EXAM0001/7/1'

    def _admin(self, monkeypatch, fetched=None, error=None):
        client = MagicMock()
        if error:
            client.get_document.side_effect = error
        else:
            client.get_document.return_value = fetched
        monkeypatch.setattr(
            'sam.integration.xras_api.admin_client.XrasAdminClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        return client

    def test_it_is_gated(self, view_only_client):
        assert view_only_client.get(self.URL).status_code == 403

    def test_a_pdf_is_served_inline_as_the_pi(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        client = self._admin(monkeypatch, fetched=(b'%PDF-1.7 body', {}))
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200 and resp.mimetype == 'application/pdf'
        assert resp.headers['Content-Disposition'].startswith('inline')
        assert 'award.pdf' in resp.headers['Content-Disposition']
        assert resp.headers['Cache-Control'] == 'private, no-store'
        assert client.get_document.call_args.kwargs['xa_user'] == 'pi-user'
        # The harness pins the write lever off in app config; the modal's gate is the flag.
        monkeypatch.setattr('webapp.dashboards.allocations.xras.modals.xras_write_configured',
                            lambda: True)
        body = auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert self.URL in body and self.URL + '?download=1' in body

    def test_download_and_non_pdf_bytes_are_attachments(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        self._admin(monkeypatch, fetched=(b'%PDF-1.7 body', {}))
        assert auth_client.get(self.URL + '?download=1').headers[
            'Content-Disposition'].startswith('attachment')
        self._admin(monkeypatch, fetched=(b'<html>not a pdf</html>', {'Content-Type': 'text/html'}))
        resp = auth_client.get(self.URL)
        assert resp.mimetype == 'application/octet-stream'
        assert resp.headers['Content-Disposition'].startswith('attachment')

    def test_a_document_not_on_the_request_is_404_without_a_fetch(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        client = self._admin(monkeypatch, fetched=(b'%PDF-', {}))
        assert auth_client.get('/allocations/xras_document/EXAM0001/7/999').status_code == 404
        client.get_document.assert_not_called()

    def test_an_outage_degrades_to_502(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        self._admin(monkeypatch, error=XrasSourceUnavailable('down'))
        assert auth_client.get(self.URL).status_code == 502


class TestTheDocumentPreview:
    """The request dialog swaps to a frame on the proxy; nothing is fetched to render it."""

    URL = '/allocations/xras_document_preview/EXAM0001/7/1'

    def test_it_is_gated(self, view_only_client):
        assert view_only_client.get(self.URL).status_code == 403

    def test_the_frame_reads_through_the_proxy(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(self.URL).get_data(as_text=True)
        assert '<iframe class="preview-frame preview-frame-page" src="/allocations/xras_document/EXAM0001/7/1"' in body
        assert 'sandbox' not in body and 'data-bs-toggle' not in body
        assert 'hx-get="/allocations/xras_request_detail/EXAM0001"' in body

    def test_a_document_not_on_the_request_says_so_in_the_dialog(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        resp = auth_client.get('/allocations/xras_document_preview/EXAM0001/7/999')
        assert resp.status_code == 200 and '<iframe' not in resp.get_data(as_text=True)

    def test_the_documents_row_offers_it_when_writes_are_on(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        detail = '/allocations/xras_request_detail/EXAM0001'
        assert self.URL not in auth_client.get(detail).get_data(as_text=True)
        monkeypatch.setattr('webapp.dashboards.allocations.xras.modals.xras_write_configured',
                            lambda: True)
        assert f'hx-get="{self.URL}"' in auth_client.get(detail).get_data(as_text=True)


class TestDocumentsOnTheRow:
    """A paperclip and a count in the Request cell; names stay in the modal."""

    def test_a_request_with_documents_shows_the_count_and_its_types(
            self, auth_client, configured):
        p = _payload('EXAM0001')
        p['actions'][0]['documents'] = [
            {'documentId': 1, 'documentType': 'Advisor_Letter', 'title': None,
             'filename': 'Letter for P Eye.pdf', 'size': 1000},
            {'documentId': 2, 'documentType': 'Supp_Info', 'title': None,
             'filename': 'cv.pdf', 'size': 2000}]
        _publish(p, _payload('EXAM0002'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'fa-paperclip' in body
        assert '2 documents: Advisor letter, Supporting information' in body
        assert body.count('fa-paperclip') == 1, 'EXAM0002 has none'
        assert 'Letter for P Eye' not in body, 'no filename on the card'


class TestSortableColumns:
    """Non-facet headers sort rows within each opportunity group, as form state."""

    def test_a_header_sorts_within_the_group_both_ways(self, auth_client, configured):
        # All three share the default opportunity -> one group.
        _publish(_payload('EXAM0003'), _payload('EXAM0001'), _payload('EXAM0002'))
        asc = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=asc').get_data(as_text=True)
        desc = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=desc').get_data(as_text=True)
        assert asc.index('EXAM0001') < asc.index('EXAM0003')
        assert desc.index('EXAM0003') < desc.index('EXAM0001')

    def test_headers_are_form_based_so_sort_survives_facet_clicks(self, auth_client,
                                                                  configured):
        _publish(_payload())
        body = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=asc').get_data(as_text=True)
        assert 'set-sort-submit' in body            # writes the form, not a URL
        assert 'data-sort-by="request"' in body

    def test_an_unknown_sort_column_is_ignored(self, auth_client, configured):
        _publish(_payload('EXAM0001'))
        assert auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=bogus&sort_dir=asc').status_code == 200


class TestCheckAll:
    """Sent with show_all: an unchecked row is outside the pending-work queue.

    The header 'Check all' batch re-check over the not-checked rows in view."""

    def test_it_rechecks_the_not_checked_rows(self, auth_client, configured,
                                              monkeypatch):
        _publish(_payload('EXAM0001'))        # no preflight -> rollup None
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        assert 'Checked 1 request' in resp.get_data(as_text=True)

    def test_nothing_to_check_when_all_are_verdicts(self, auth_client, configured):
        _publish_one_verdict('failed', messages=['x'])   # a real verdict, not None
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'Nothing to check' in resp.get_data(as_text=True)

    def test_it_degrades_200_when_xras_is_unreachable(self, auth_client, configured,
                                                      monkeypatch):
        _publish(_payload('EXAM0001'))
        client = _reader(monkeypatch)
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'could not be reached' in resp.get_data(as_text=True)

    def test_it_honors_a_wide_window_passed_in_the_post_body(
            self, auth_client, configured, monkeypatch):
        # The row is out of the DEFAULT window but in a wide one. The card sends
        # the window in the POST BODY (hx-include), so the route must read
        # request.values, not request.args — else it silently reports "nothing to
        # check" over a wide-filter view full of not-checked rows.
        _publish(_payload('EXAM0001', submit_date='2015-01-01T00:00:00Z'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        # default window sees nothing
        assert 'Nothing to check' in auth_client.post(
            '/allocations/xras_recheck_visible').get_data(as_text=True)
        # a wide window in the body picks it up
        resp = auth_client.post(
            '/allocations/xras_recheck_visible',
            data={'show_all': '1', 'start_date': '2000-01-01', 'end_date': '2030-01-01'})
        assert 'Checked 1 request' in resp.get_data(as_text=True)


class TestPendingWorkToggle:
    """Default is the pending-work queue (XRAS admin's Recent submissions);
    `show_all=1` restores everything, with the date filter."""

    def test_a_posted_request_is_hidden_by_default(self, auth_client, configured):
        _publish_with_verdict(_payload('EXAM0001'), push_state='seen_in_log')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'No pending work' in body
        assert 'EXAM0001' not in body.split('id="xras-remediation-show-all"')[1]

    @staticmethod
    def _owed():
        # Entered after the repoint, so an `unknown` push state is owed (is_pending_work).
        payload = _payload('EXAM0001')
        payload['actions'][0]['entryDate'] = '2026-11-01T00:00:00Z'
        return payload

    @staticmethod
    def _log(monkeypatch, status):
        monkeypatch.setattr('sam.xras.preflight.log_seen_for', lambda session, ids: {
            7: {'status': status, 'received_time': '2026-11-10T11:28:24', 'log_id': 481}})

    def test_a_push_the_log_has_processed_leaves_the_queue(self, auth_client, configured,
                                                           monkeypatch):
        # UTAM0017: the snapshot still says pending; the log already has the processed push.
        _publish_with_verdict(self._owed(), push_state='unknown')
        self._log(monkeypatch, 'processed')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'No pending work' in body

    def test_a_failed_post_stays_in_the_queue(self, auth_client, configured, monkeypatch):
        _publish_with_verdict(self._owed(), push_state='unknown')
        self._log(monkeypatch, 'failed')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'EXAM0001' in body and 'No pending work' not in body

    def test_a_landed_push_links_to_its_log_row(self, auth_client, configured, monkeypatch):
        _publish_with_verdict(self._owed(), push_state='unknown')
        self._log(monkeypatch, 'processed')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras_action_details/481' in body and 'log #481' in body

    def test_the_readiness_dialog_links_the_log_without_a_toggle(self, auth_client,
                                                                 configured, monkeypatch):
        _publish_with_verdict(self._owed(), push_state='unknown')
        self._log(monkeypatch, 'processed')
        body = auth_client.get('/allocations/xras_readiness_detail/EXAM0001').get_data(as_text=True)
        link = re.search(r'<a [^>]*xras_action_details/481[^>]*>', body)
        assert link and 'data-bs-toggle' not in link.group(0)   # PR #464: inside an open modal
        assert 'not yet pushed' not in body

    def test_show_everything_reveals_it(self, auth_client, configured):
        _publish_with_verdict(_payload('EXAM0001'), push_state='seen_in_log')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'EXAM0001' in body
        assert 'checked' in body.split('id="xras-remediation-show-all"')[1].split('>')[0]

    def test_an_in_flight_request_is_pending_whatever_its_age(self, auth_client,
                                                              configured):
        # No date window in the queue: a 2015 submission still in review is work.
        _publish_with_verdict(_payload('EXAM0001', action_status='Submitted',
                                       submit_date='2015-01-01T00:00:00Z'),
                              push_state='unknown')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'EXAM0001' in body and 'No pending work' not in body
        assert 'outside the date filter' not in body
        # ... and the date filter hides it once everything is shown.
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'outside the date filter' in body

    def test_the_header_counts_pending_and_the_rest(self, auth_client, configured):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(_payload('EXAM0001', action_status='Submitted'),
                                         pending_push=True,
                                         preflights={7: {'status': 'incomplete',
                                                         'push_state': 'unknown'}}),
                     request_index_entry(_payload('EXAM0002'), pending_push=False,
                                         preflights={7: {'status': 'rechecked',
                                                         'push_state': 'seen_in_log',
                                                         'push_detail': _posted('seen_in_log')}})]})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert '1 pending' in body
        assert '1 more with Everything in the window' in body

    def test_the_batch_recheck_honors_the_toggle(self, auth_client, configured,
                                                 monkeypatch):
        # An unchecked row is never pending work (no verdict = outside the sweep
        # window), so "Check all" only finds it under Show everything.
        _publish(_payload('EXAM0001'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        assert 'Nothing to check' in auth_client.post(
            '/allocations/xras_recheck_visible').get_data(as_text=True)
        resp = auth_client.post('/allocations/xras_recheck_visible',
                                data={'show_all': '1'})
        assert 'Checked 1 request' in resp.get_data(as_text=True)

    def test_the_switch_explains_both_sets_in_a_popover(self, auth_client, configured):
        _publish(_payload())
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        after = body.split('id="xras-remediation-show-all"')[1][:2500]
        assert 'data-bs-toggle="popover"' in after
        assert 'pending work only' in after and 'everything the sweep found' in after

    def test_the_switch_belongs_to_the_filter_form(self, auth_client, configured):
        _publish(_payload())
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        switch = body.split('id="xras-remediation-show-all"')[1].split('>')[0]
        assert 'name="show_all"' in switch and 'form="xras-remediation-filters"' in switch


class TestMnemonicUnblockStrip:
    """The § 2.2 ranking strip on the Remediations card — points at Admin to fix."""

    def _publish_mnemonic_failure(self, pi_username):
        from sam.xras.errors import mnemonic_internal_failed
        payload = _payload('EXAM0001')
        payload['roles'][0]['person']['username'] = pi_username
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': [mnemonic_internal_failed()], 'gaps': [],
                   'service': 'add', 'stage': 'Approved', 'action_status': 'Approved',
                   'request_status': 'Approved', 'push_state': 'pending',
                   'push_detail': None, 'resolved': None,
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})

    def test_an_unknown_pi_renders_the_no_affiliation_line(self, auth_client,
                                                           configured):
        # A PI SAM does not know can't resolve an org -> the 'unresolved' bucket,
        # which the strip surfaces as the no-affiliation line. Deterministic
        # without committing users.
        self._publish_mnemonic_failure('ghost-user-strip-xyz')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no current affiliation' in body

    def test_view_only_is_still_forbidden(self, view_only_client, configured):
        self._publish_mnemonic_failure('ghost-user-strip-xyz')
        assert view_only_client.get(FRAGMENT).status_code == 403


class TestContractUnblockStrip:
    """The contract-blockers strip — the mnemonic strip's shape, linking to Admin -> Contracts."""

    def _publish_contract_failure(self, grants, push_state='pending'):
        payload = _payload('EXAM0001')
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': ['x'], 'gaps': [],
                   'service': 'add', 'stage': 'Approved', 'action_status': 'Approved',
                   'request_status': 'Approved', 'push_state': push_state,
                   'push_detail': _posted(push_state),
                   'resolved': {'unresolved_grants': list(grants)},
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})

    @staticmethod
    def _grant(number, **over):
        grant = {'number': number, 'core': number, 'reason': 'missing',
                 'candidates': [], 'agency': None, 'title': 'Seeded Title',
                 'pi_name': 'P. Eye', 'begin_date': '2026-01-01',
                 'end_date': '2027-12-31', 'is_pending': False}
        grant.update(over)
        return grant

    def test_a_reference_links_to_a_seeded_manual_form(self, auth_client, configured):
        self._publish_contract_failure([self._grant('ISS 25-643')])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'id="xras-contract-strip"' in body
        assert 'ISS 25-643' in body
        assert '/admin/contracts?' in body and 'create=ISS' in body
        assert 'mode=manual' in body and 'title=Seeded' in body
        assert 'start_date=2026-01-01' in body and 'end_date=2027-12-31' in body

    def test_an_nsf_award_links_to_lookup_mode(self, auth_client, configured):
        self._publish_contract_failure([self._grant(
            '9980401', agency='National Science Foundation')])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'create=9980401' in body and 'mode=lookup' in body

    def test_a_tie_renders_as_a_variant_with_no_create_link(self, auth_client,
                                                            configured, monkeypatch):
        # The route's db.session cannot see rows made in the test SAVEPOINT, so
        # the live re-check is stubbed; the tie itself is covered in
        # test_xras_contract_report.py.
        monkeypatch.setattr('sam.queries.xras_contract_report.contract_resolution',
                            lambda session, number, core: 'ambiguous')
        self._publish_contract_failure([self._grant(
            'NSF-9980402', core='9980402', reason='ambiguous',
            candidates=['9980402', 'PLR-9980402'])])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'NSF-9980402' in body and 'spelling variant' in body
        assert 'create=NSF-9980402' not in body

    def test_the_strip_explains_itself_in_a_popover(self, auth_client, configured):
        self._publish_contract_failure([self._grant('ISS 25-643')])
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        strip = body.split('id="xras-contract-strip"')[1][:3000]
        assert 'data-bs-toggle="popover"' in strip and 'Contracts to create' in strip

    def test_the_strip_follows_the_rows_in_view(self, auth_client, configured):
        # A failing row that is NOT pending work (already posted) feeds the strip
        # only under Show everything — the strip ranks fixes for what is in view.
        self._publish_contract_failure([self._grant('ISS 25-643')], push_state='seen_in_log')
        assert 'xras-contract-strip' not in auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'xras-contract-strip' in auth_client.get(
            FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)

    def _publish_with_messages(self, grants, messages):
        payload = _payload('EXAM0001', placeholder=False)
        verdict = {'status': 'failed', 'would_succeed': False, 'messages': list(messages),
                   'gaps': [], 'service': 'add', 'stage': 'Approved',
                   'action_status': 'Approved', 'request_status': 'Approved',
                   'push_state': 'pending', 'push_detail': None,
                   'resolved': {'unresolved_grants': list(grants)},
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'], 'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True, preflights={7: verdict})]})

    def test_an_nsf_award_that_is_the_only_blocker_offers_create(self, auth_client,
                                                                 configured):
        self._publish_with_messages([self._grant('9980403', agency='NSF')],
                                    ['Cannot find contract for grant number "9980403" ("9980403")'])
        strip = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(
            as_text=True).split('id="xras-contract-strip"')[1][:4000]
        assert '/xras_contract_create_form?number=9980403' in strip
        assert 'requests=EXAM0001' in strip and 'waits on' not in strip

    def test_another_blocker_still_offers_create(self, auth_client, configured):
        """Ben 2026-11-07: the post links a contact PI later, so another blocker need not wait."""
        self._publish_with_messages([self._grant('9980404', agency='NSF')],
                                    ['PI ghost-pi is not in database'])
        strip = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(
            as_text=True).split('id="xras-contract-strip"')[1][:4000]
        assert 'xras_contract_create_form?number=9980404' in strip and 'waits on' not in strip

    def test_a_reference_never_offers_create(self, auth_client, configured):
        self._publish_with_messages([self._grant('ISS 25-643')], [])
        strip = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(
            as_text=True).split('id="xras-contract-strip"')[1][:4000]
        assert 'xras_contract_create_form' not in strip

    def test_the_dialog_shows_the_award_and_its_people(self, auth_client, configured,
                                                       monkeypatch):
        from datetime import date
        from sam.integration.awards.base import AwardRecord, PersonRef
        record = AwardRecord(provenance='NSF Awards API', contract_number='AGS-9980405',
                             title='Clouds', start_date=date(2026, 9, 1),
                             end_date=date(2029, 8, 31),
                             pi=PersonRef(name='Nobody Atall', email='nobody@uni.example'),
                             monitor=PersonRef(name='Olive Officer', email='oo@nsf.example'))
        monkeypatch.setattr('sam.integration.awards.resolve_award',
                            lambda source, number, exact=False: record)
        self._publish_with_messages([self._grant('9980405', agency='NSF')], [])
        body = auth_client.get('/allocations/xras_contract_create_form', query_string={
            'number': '9980405', 'requests': 'EXAM0001', 'pi_name': 'Nobody Atall'}
        ).get_data(as_text=True)
        assert 'AGS-9980405' in body and 'Clouds' in body
        assert 'Nobody Atall' in body and 'new contact' in body
        assert 'Create contract' in body and 'disabled' not in body.split('Create contract')[0][-200:]

    def test_the_dialog_refuses_a_different_pi(self, auth_client, configured, monkeypatch):
        from datetime import date
        from sam.integration.awards.base import AwardRecord, PersonRef
        record = AwardRecord(provenance='NSF Awards API', contract_number='AGS-9980406',
                             title='Clouds', start_date=date(2026, 9, 1),
                             pi=PersonRef(name='Sam Roe', email='sr@uni.example'))
        monkeypatch.setattr('sam.integration.awards.resolve_award',
                            lambda source, number, exact=False: record)
        self._publish_with_messages([self._grant('9980406', agency='NSF')], [])
        body = auth_client.get('/allocations/xras_contract_create_form', query_string={
            'number': '9980406', 'requests': 'EXAM0001', 'pi_name': 'Pat Doe'}
        ).get_data(as_text=True)
        assert 'may be a different award' in body

    def test_the_post_reads_the_award_while_another_blocker_remains(self, auth_client,
                                                                    configured, monkeypatch):
        # A refused plan, so the route (which commits) writes nothing.
        from datetime import date
        from sam.integration.awards.base import AwardRecord, PersonRef
        record = AwardRecord(provenance='NSF Awards API', contract_number='AGS-9980407',
                             title='Clouds', start_date=date(2026, 9, 1),
                             pi=PersonRef(name='Sam Roe', email='sr@uni.example'))
        monkeypatch.setattr('sam.integration.awards.resolve_award',
                            lambda source, number, exact=False: record)
        self._publish_with_messages([self._grant('9980407', agency='NSF')],
                                    ['PI ghost-pi is not in database'])
        body = auth_client.post('/allocations/xras_contract_create', data={
            'number': '9980407', 'requests': 'EXAM0001', 'pi_name': 'Pat Doe'}
        ).get_data(as_text=True)
        assert 'may be a different award' in body and 'also blocked on' not in body

    def test_no_grants_means_no_strip(self, auth_client, configured):
        self._publish_contract_failure([])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras-contract-strip' not in body

    def test_view_only_is_still_forbidden(self, view_only_client, configured):
        self._publish_contract_failure([self._grant('ISS 25-643')])
        assert view_only_client.get(FRAGMENT).status_code == 403


class TestItIsACardNotATab:

    def test_the_worklist_still_has_exactly_two_tabs(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        pane = body.split('id="xrasWorklistTabs"')[1].split('</ul>')[0]
        assert pane.count('data-bs-toggle="tab"') == 2

    def test_the_card_sits_outside_the_tab_content(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert body.index('alloc-xras-remediations') \
            > body.index('alloc-xras-accounts')


# the collapse affordance

class TestEveryExpandableRowShowsAChevron:
    """Every expandable table needs a visible chevron.

    A `cursor-pointer` row announces itself only to someone already hovering
    it, so a table without the chevron has no visible affordance at all. The
    chevron is `.collapse-icon` and nothing else: components.css rotates it
    off the `aria-expanded` Bootstrap writes onto the trigger, which works for
    a toggle on the `<tr>` and for one on a `<td>`, and survives an htmx swap
    with no registration anywhere.
    """

    PARTIALS = (REPO_ROOT / 'src' / 'webapp'
                / 'templates' / 'dashboards' / 'allocations' / 'partials')

    @pytest.mark.parametrize('name', [
        'xras_activity_card.html',
        'xras_accounts_card.html',
        'xras_remediations_card.html',
        'xras_table.html',
    ])
    def test_each_expandable_table_offers_one(self, name):
        text = (self.PARTIALS / name).read_text()
        assert 'collapse-icon' in text or 'drill_toggle(' in text

    @pytest.mark.parametrize('name', [
        'xras_activity_card.html', 'xras_table.html'])
    def test_none_still_uses_the_js_driven_marker(self, name):
        """`.xras-collapse-icon` rotated via an admin-cards.js registration
        that no longer exists. The class alone renders a chevron that never
        moves — a silent, plausible-looking regression."""
        assert 'xras-collapse-icon' not in (self.PARTIALS / name).read_text()


# facet / control parity

class TestFacetParity:
    """The chip-to-control check is tests/unit/gates/test_facet_form_contract.py."""

    def test_the_search_box_is_bound_to_that_form(self, auth_client, armed):
        """It renders inside the card and belongs to the form outside it.

        `form=` is what makes a chip click carry the search term: the chip
        submits `#xras-remediation-filters`, and htmx serializes a form through
        `form.elements` / `FormData`, both of which include form-associated
        controls wherever they sit in the document. Drop the attribute and
        every chip silently resets the search.
        """
        _publish(_payload())
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        box = body.split('id="xras-remediation-search"')[1].split('>')[0]
        assert 'name="search"' in box
        assert 'form="xras-remediation-filters"' in box


# the four empty states

class TestTheFourEmptyStates:
    """Collapsing any two of these would mislead an operator."""

    def test_unconfigured(self, auth_client, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'not configured' in body

    def test_no_sweep_at_all(self, auth_client, configured):
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'No sweep has published yet' in body

    def test_a_worklist_without_an_index(self, auth_client, configured):
        """The hour after deploy, and the independent-failure case the two
        cache keys exist for. A lie if reported as 'nothing to remediate'."""
        xras_cache.store_pending_worklist({'rows': [], 'counts': {}})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'did not publish this list' in body

    def test_published_and_empty(self, auth_client, configured):
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Nothing to remediate' in body

    def test_the_four_bodies_are_distinct(self, auth_client, configured,
                                          monkeypatch):
        seen = set()
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        xras_cache.store_pending_worklist({'rows': []})
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        assert len(seen) == 4


# rendering

class TestRendering:

    # The roster/action offers moved from the card's per-request expansion into
    # the detail modal (the card row now just links to it), so these behaviors
    # are asserted against the modal body — fed by a live read (`_reader`).
    def test_the_modal_renders_the_offers(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'EXAM0001' in body
        assert 'Withdraw…' in body
        assert 'Resolve identity (merge)…' in body

    def test_the_lever_off_disables_rather_than_hides(self, auth_client,
                                                      configured, monkeypatch):
        """A control that vanishes teaches nobody that a switch exists."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Withdraw…' in body
        assert 'XRAS_WRITE_ENABLED' in body
        assert 'disabled' in body

    def test_a_drafted_action_offers_resubmit_instead(self, auth_client, armed,
                                                       monkeypatch):
        _reader(monkeypatch, payload=_payload(action_status='Incomplete'))
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Re-submit…' in body
        assert 'Withdraw…' not in body

    def test_an_unreconciled_placeholder_offers_no_merge(self, auth_client,
                                                         armed, monkeypatch):
        """That row means 'create the account' — the healthy path."""
        _reader(monkeypatch, payload=_payload(reconciled=False))
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Resolve identity' not in body
        assert 'needs an account' in body

    def test_the_window_says_what_it_hid(self, auth_client, armed):
        """WARNING: On this card the hidden rows skew URGENT — never hide silently."""
        _publish(_payload(), _payload('EXAM0002', submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'outside the date filter' in body

    def test_a_card_emptied_by_the_window_says_so_rather_than_nothing_to_do(
            self, auth_client, armed):
        """The default lookback hides stale rows, which are the point here."""
        _publish(_payload(submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'Nothing to remediate' not in body

    def test_a_row_with_no_submit_date_survives_the_window(self, auth_client,
                                                            armed):
        """Missing information is not evidence of age."""
        payload = _payload()
        payload['submitDate'] = None
        _publish(payload)
        body = auth_client.get(
            FRAGMENT + '?show_all=1&start_date=2026-08-01').get_data(as_text=True)
        assert 'EXAM0001' in body

    def test_a_patched_row_is_marked_as_newer_than_the_sweep(self, auth_client,
                                                             armed):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'rows': [
                request_index_entry(_payload(), refreshed_at=datetime.now())]})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'newer than the sweep' in body

    def test_status_chips_filter(self, auth_client, armed):
        _publish(_payload('EXAM0001'),
                 _payload('EXAM0002', status='Submitted', action_status='Submitted'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted').get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body

    def test_status_is_the_in_flight_actions_not_the_requests(self, auth_client, armed):
        """NRAL0035, 2026-11-04: requestStatus stays Approved while its Extension waits."""
        payload = _payload('EXAM0003')
        payload['actions'].append({'actionId': 8, 'actionType': 'Extension',
                                   'actionStatus': 'Submitted'})
        _publish(payload, _payload('EXAM0001'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted').get_data(as_text=True)
        assert 'EXAM0003' in body and 'EXAM0001' not in body
        assert 'title="Request: Approved"' in body

    def test_an_old_snapshot_filters_on_the_request_status(self, auth_client, armed):
        row = request_index_entry(_payload('EXAM0002', status='Submitted'))
        del row['latest_action_status']
        xras_cache.store_requests_index({'generated_at': datetime.now(), 'rows': [row]})
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted').get_data(as_text=True)
        assert 'EXAM0002' in body

    def test_the_type_column_shows_the_in_flight_action_not_a_count(
            self, auth_client, armed):
        """The card shows the request's current action type, not how many actions
        it has — multiplicity now lives in the request modal."""
        payload = _payload()
        payload['actions'].append({'actionId': 8, 'actionType': 'Extension',
                                   'actionStatus': 'Approved'})
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Extension' in body          # the latest (highest-id) action type
        assert '2 actions' not in body      # the old count note is gone

    def test_the_project_admin_column_shows_the_allocation_manager(
            self, auth_client, armed):
        """SAM's "Project Admin" is the XRAS Allocation Manager (roleTypeId 14),
        shown when the request names one."""
        payload = _payload()
        payload['roles'].append(
            {'person': {'username': 'am-user', 'firstName': 'Al',
                        'lastName': 'Manager', 'isReconciled': True},
             'roles': [{'roleId': 3, 'role': 'Allocation Manager',
                        'roleTypeId': 14}]})
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Project admin' in body      # the column header
        assert 'Al Manager' in body         # the resolved admin

    def test_the_project_admin_column_is_blank_without_one(self, auth_client,
                                                           armed):
        """The base payload names no Allocation Manager — the cell is a dash,
        and the header still renders."""
        _publish(_payload())
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Project admin' in body


# the project link

class TestTheSamBadgeLinksWhenTheProjectExists:
    """`pending_push` is not a display choice — it is the sweep's set
    difference of `requestNumber` against `Project.projcode`. So the badge
    already asserts "a SAM project exists"; the link is that same claim, made
    clickable, and costs no query.

    Two states, not the action log's three. The flag is only as fresh as the
    last sweep and the post-write patch copies it rather than re-deriving
    (`_still_pending` defaults to True), so a stale row can withhold a link it
    could have offered but can never offer one to a project that is not there.
    """

    def test_a_pushed_request_links_to_its_project(self, auth_client, armed):
        _publish(_payload('EXAM0001'), pending=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/user/project-details-modal/EXAM0001' in body
        assert 'data-bs-target="#projectDetailsModal"' in body
        assert 'hx-target="#projectDetailsModalBody"' in body

    def test_a_pending_request_is_a_plain_badge(self, auth_client, armed):
        """WARNING: The direction that matters. `pending_push` is exactly "SAM has
        no project for this number" — the reason most rows are on this card at
        all. A link would 404 the modal on the majority of the table."""
        _publish(_payload('EXAM0001'), pending=True)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no project' in body
        assert 'projectDetailsModal' not in body

    def test_the_project_link_reads_the_code(self, auth_client, armed):
        _publish(_payload('EXAM0001'), pending=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '<strong>EXAM0001</strong>' in body
        assert '<strong>project</strong>' not in body

    @staticmethod
    def _publish_new(series, *, status='rechecked', messages=()):
        payload = _payload('NCAR4407')
        payload['actions'][0]['actionType'] = 'New'
        verdict = {'status': status, 'would_succeed': status == 'rechecked',
                   'messages': list(messages), 'gaps': [], 'service': 'new', 'stage': 'Approved',
                   'push_state': 'pending', 'push_detail': None,
                   'resolved': {'series': series} if series else None,
                   'checked_at': '2026-10-19T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})

    @pytest.mark.parametrize('status', ['rechecked', 'failed'])
    def test_a_pending_new_shows_the_series_it_would_mint(self, auth_client, armed,
                                                          status):
        """Whatever the verdict: a row failing on a person still names its mnemonic match."""
        self._publish_new('UCSG', status=status)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '<span class="font-monospace">UCSG####</span>' in body
        assert 'no project' not in body
        assert 'projectDetailsModal' not in body

    def test_a_mnemonic_blocked_new_says_no_match(self, auth_client, armed):
        from sam.xras.errors import MNEMONIC_EXTERNAL_PREFIX
        self._publish_new(None, status='failed',
                          messages=[MNEMONIC_EXTERNAL_PREFIX + ': Some University'])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no match' in body
        assert 'no project' not in body
        assert '####' not in body

    @pytest.mark.parametrize('status,messages', [('failed', ['PI pi-user is not in database']),
                                                 ('incomplete', [])])
    def test_no_series_without_a_mnemonic_blocker_says_no_project(self, auth_client, armed,
                                                                  status, messages):
        """A missing PI leaves no series too, and an incomplete check never tried: neither is `no match`."""
        self._publish_new(None, status=status, messages=messages)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no project' in body
        assert 'no match' not in body

    def test_the_request_column_opens_the_detail_modal(self, auth_client, armed):
        """The request number IS the XRAS request, so it opens the read-only
        detail modal — even when a SAM project by that name exists (the SAM
        cell keeps the project link). It is no longer a collapse trigger."""
        _publish(_payload('EXAM0001'), pending=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/allocations/xras_request_detail/EXAM0001' in body
        assert 'data-bs-target="#xrasModal"' in body
        # The SAM cell still links the found project — Request vs Result.
        assert '/user/project-details-modal/EXAM0001' in body


class TestTheOpportunityGroupCollapses:
    """The per-request expansion was folded into the detail modal (the Request
    cell opens it). The opportunity group is the collapsible layer now, default
    open: its header row carries the toggle — it holds no link, so a row-level
    toggle is safe — and the member rows live in a sibling `collapse show`
    tbody keyed on the numeric opportunity id."""

    def test_the_group_header_is_the_toggle_default_open(self, auth_client,
                                                         armed):
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-target="#xopp-5"' in body
        assert 'aria-expanded="true"' in body
        assert '<tbody id="xopp-5" class="collapse show">' in body

    def test_the_per_request_expansion_is_gone(self, auth_client, armed):
        """No `#xrem-<id>` collapse rows and no row include — the request row is
        a single line that opens the modal, so the roster/actions expansion no
        longer ships in the card (it lives in the modal)."""
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '#xrem-' not in body

    def test_the_group_id_is_numeric_not_the_free_text_number(
            self, auth_client, armed):
        """Submitted numbers can be free text with spaces ('New University
        Large Request - Fall 2017 …' is live) — an unmatchable id. The
        collapsible layer is keyed on the numeric opportunity id, never the
        request number."""
        payload = _payload('New University Large Request - Fall 2017 Zhong',
                           status='Submitted')
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-target="#xopp-5"' in body
        assert 'id="xopp-New' not in body


# the search box

class TestSearch:
    """One box over the fields an operator arrives holding.

    The request number IS the projcode — the sweep resolves a handoff by
    ``Project.projcode == requestNumber`` — so there is no second identifier
    to search for. The other two are people, and one of them is invisible
    until a row is expanded.
    """

    def test_it_matches_the_request_number(self, auth_client, armed):
        _publish(_payload('EXAM0001'), _payload('EXAM0002'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=exam0002').get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body

    def test_it_matches_the_project_lead_by_display_name(self, auth_client,
                                                          armed):
        """The column shows a name, so a name is what gets typed."""
        _publish(_payload('EXAM0001'), _payload('EXAM0002'))
        body = auth_client.get(FRAGMENT + '?show_all=1&search=Eye').get_data(as_text=True)
        assert 'EXAM0001' in body and 'EXAM0002' in body

    def test_it_matches_a_roster_member_the_row_does_not_show(
            self, auth_client, armed):
        """WARNING: The reason a request is on this card is usually one person on
        its roster, and that person is not rendered until the row is expanded.
        A search that only saw the summary row would miss every one of them."""
        _publish(_payload('EXAM0001'),
                 _payload('EXAM0002', placeholder=False))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=ghost-user').get_data(as_text=True)
        assert 'EXAM0001' in body and 'EXAM0002' not in body

    def test_a_search_that_matches_nothing_keeps_its_own_box(self, auth_client,
                                                              armed):
        """Otherwise the only way out of a typo is a page reload."""
        _publish(_payload())
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=nothingmatchesthis').get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'id="xras-remediation-search"' in body
        assert 'value="nothingmatchesthis"' in body

    def test_the_chips_survive_emptying_the_card(self, auth_client, armed):
        """The copy says "clear the search and chips"; both must be there to
        clear. Rendering them only alongside rows makes them vanish at exactly
        the moment they are needed."""
        _publish(_payload('EXAM0001'), _payload('EXAM0002', status='Submitted'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted&search=EXAM0001').get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'facet-chip' in body

    def test_the_date_badge_counts_only_what_the_date_hid(self, auth_client,
                                                          armed):
        """WARNING: Measured against `total` it would grow with every keystroke and
        blame the window for the search."""
        _publish(_payload('EXAM0001'),
                 _payload('EXAM0002', submit_date='2015-01-01T00:00:00Z'))
        plain = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '1 outside the date filter' in plain

        searched = auth_client.get(
            FRAGMENT + '?show_all=1&search=zzz').get_data(as_text=True)
        assert '1 outside the date filter' in searched
