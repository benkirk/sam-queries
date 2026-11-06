"""The Remediations card's request editor (Part B), the per-request overrides, the destructive
lifecycle (Part C, ADMIN_XRAS) and POST validation. Write happy paths live in the service tests.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api.base import XrasSourceUnavailable

# The shared fixtures and payload builders; `_cache` is the autouse cache reset.
from _remediations_support import (  # noqa: F401
    _cache,
    FRAGMENT,
    manage_not_admin_client,
    configured,
    armed,
    _payload,
    _publish,
    _detail_payload,
    _reader,
)

# the request editor (Part B)

def _with_deleted_action():
    """Action 7 soft-deleted (still listed, isDeleted) beside a live action 8, as XRAS keeps an abort."""
    payload = _detail_payload()
    payload['actions'][0]['isDeleted'] = True
    payload['actions'][0]['actionStatus'] = 'Incomplete'
    payload['actions'].append({'actionId': 8, 'actionType': 'New',
                               'actionStatus': 'Approved'})
    return payload


class TestTheHeadlineActionAndXrasLinks:
    """The headline action (status, hover note) and the quick links out to the XRAS admin app."""

    def _body(self, auth_client, monkeypatch, payload):
        _reader(monkeypatch, payload=payload)
        return auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)

    def test_the_footer_opens_the_latest_action(self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['actions'].append({'actionId': 9, 'actionType': 'Extension',
                                   'actionStatus': 'Submitted'})
        body = self._body(auth_client, monkeypatch, payload)
        footer = body[body.index('id="xrasModalFooter"'):]
        assert '/actions/9"' in footer and 'Open in XRAS' in footer
        assert 'Open in XRAS' not in body[:body.index('id="xrasModalFooter"')]
        assert 'xras.example.invalid' not in body, 'publicURL is not a link into XRAS'

    def test_each_action_links_its_page_and_process_tab(self, auth_client, armed, monkeypatch):
        body = self._body(auth_client, monkeypatch, _with_deleted_action())
        assert '/actions/8/show_process"' in body
        assert '/actions/7"' not in body, 'a soft-deleted action is a 404 in XRAS'

    def test_no_link_sits_inside_the_collapse_button(self, auth_client, armed, monkeypatch):
        body = self._body(auth_client, monkeypatch, _detail_payload())
        for head in re.findall(r'<button[^>]*xras-action-head.*?</button>', body, re.S):
            assert '<a ' not in head

    def test_the_header_names_the_latest_actions_status_and_type(
            self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['actions'].append({'actionId': 9, 'actionType': 'Extension',
                                   'actionStatus': 'Submitted'})
        body = self._body(auth_client, monkeypatch, payload)
        header = body[body.index('xras-request-meta'):body.index('xras-request-title')]
        assert '>Submitted<' in header and '>Extension<' in header
        assert 'request approved' in header and '>New<' not in header

    def test_a_pending_action_outranks_a_newer_applied_one(self, auth_client, armed, monkeypatch):
        """UWKU0002, 2026-11-04: Supplement 399592 Submitted, 403457 Approved; the card says Submitted."""
        payload = _detail_payload()
        payload['actions'] += [{'actionId': 9, 'actionType': 'Supplement', 'actionStatus': 'Submitted'},
                               {'actionId': 10, 'actionType': 'Supplement', 'actionStatus': 'Approved'}]
        body = self._body(auth_client, monkeypatch, payload)
        header = body[body.index('xras-request-meta'):body.index('xras-request-title')]
        assert '>Submitted<' in header and 'Newer Supplement 10: Approved' in header
        assert '/actions/9" target="_blank"' in body[body.index('id="xrasModalFooter"'):]

    def test_the_headline_actions_section_opens_beside_the_latest(
            self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['actions'] += [{'actionId': 9, 'actionType': 'Supplement', 'actionStatus': 'Submitted'},
                               {'actionId': 10, 'actionType': 'Supplement', 'actionStatus': 'Approved'}]
        body = self._body(auth_client, monkeypatch, payload)
        assert 'class="collapse show" id="xras-action-9"' in body, 'the headline action'
        assert 'class="collapse show" id="xras-action-10"' in body, 'the latest keeps its badge and stays open'
        assert 'class="collapse" id="xras-action-7"' in body or 'class="collapse" id="xras-action-8"' in body
        assert body.count('>latest<') == 1

    def test_the_card_names_the_newer_action_on_hover(self, auth_client, armed):
        payload = _payload('EXAM0004')
        payload['actions'] += [{'actionId': 8, 'actionType': 'Supplement', 'actionStatus': 'Submitted'},
                               {'actionId': 9, 'actionType': 'Supplement', 'actionStatus': 'Approved',
                                'entryDate': datetime.now().strftime('%Y-%m-%dT01:34:47Z')}]
        _publish(payload)
        body = auth_client.get(FRAGMENT + '?show_all=1&status=Submitted').get_data(as_text=True)
        assert (f"Request: Approved · Newer Supplement 9 (entered {datetime.now():%Y-%m-%d}): Approved"
                in body), 'the date is when XRAS received it, not an approval date (XRAS keeps none)'

    def test_a_roster_username_searches_xras(self, auth_client, armed, monkeypatch):
        body = self._body(auth_client, monkeypatch, _detail_payload())
        assert '/search_all?q=ghost-user-abcde"' in body


class TestASoftDeletedActionIsNotWritable:
    """XRAS-dev, 2026-10-14: a soft-deleted action takes resource, comment and date writes,
    validates and submits, and the admin app then 404s it. SAM is the only guard."""

    def test_the_detail_modal_offers_no_editor_on_it(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_with_deleted_action())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_dates_form/EXAM0001/7' not in body
        assert 'xras_resource_remove/EXAM0001/7/' not in body

    @pytest.mark.parametrize('form', ['xras_withdraw_form', 'xras_resubmit_form'])
    def test_the_action_modals_refuse_it(self, auth_client, armed, monkeypatch, form):
        _reader(monkeypatch, payload=_with_deleted_action())
        body = auth_client.get(f'/allocations/{form}/EXAM0001/7').get_data(as_text=True)
        assert 'deleted in XRAS' in body

    def test_the_withdraw_count_ignores_it(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_with_deleted_action())
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/8').get_data(as_text=True)
        assert "the request's only action" in body

    @pytest.mark.parametrize('url, data', [
        ('/allocations/xras_resource_edit/EXAM0001/7/530201',
         {'amount': '20', 'stage': 'Requested'}),
        ('/allocations/xras_dates_edit/EXAM0001/7',
         {'begin_date': '2026-01-01', 'end_date': '2026-12-31'}),
        ('/allocations/xras_action_fields_edit/EXAM0001/7', {'user_comments': 'x'}),
    ])
    def test_a_write_to_it_is_refused_before_xras(self, auth_client, armed, monkeypatch,
                                                  url, data):
        from sam.manage import xras_remediation
        for op in ('update_resource_amount', 'set_action_dates', 'update_action'):
            monkeypatch.setattr(xras_remediation, op, MagicMock(
                side_effect=AssertionError(f'{op} reached for a deleted action')))
        _reader(monkeypatch, payload=_with_deleted_action())
        body = auth_client.post(url, data=data).get_data(as_text=True)
        assert 'deleted in XRAS' in body


class TestTheEditors:
    """The amount/date editors: forms render, validation bites, the lever and
    the admin-context ceiling both refuse. Write happy-paths live at the
    service layer (test_xras_remediation_service.py), per the house rule."""

    def test_the_amount_form_renders(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_resource_form/EXAM0001/7/530201'
            '?stage=Requested').get_data(as_text=True)
        assert 'Edit amount <span class="modal-title-meta">EXAM0001</span>' in body
        assert 'Cheyenne' in body
        assert 'name="amount"' in body

    def test_the_dates_form_renders(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_dates_form/EXAM0001/7').get_data(as_text=True)
        assert 'allocation dates <span class="modal-title-meta">EXAM0001</span>' in body
        assert 'name="begin_date"' in body

    def test_the_detail_modal_offers_editors_on_requested_rows(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        # Writes off here: the figures are plain, the other editors render disabled.
        assert 'data-xras-amount' not in body
        assert 'xras_resource_remove/EXAM0001/7/530201' in body  # Remove
        assert 'xras_dates_form/EXAM0001/7' in body             # Set dates…

    def test_with_writes_on_the_requested_amount_is_its_own_editor(
            self, auth_client, armed, monkeypatch):
        from webapp.dashboards.allocations.xras import modals
        monkeypatch.setattr(modals, 'xras_write_configured', lambda *a: True)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert body.count('data-xras-amount') == 1              # Requested only
        assert 'hx-post="/allocations/xras_resource_edit/EXAM0001/7/530201"' in body
        assert 'xras_resource_form/EXAM0001/7/530201?stage=Requested' in body  # with a comment
        assert 'name="stage" value="Approved"' not in body       # the award stays locked

    def test_the_award_editor_is_locked_without_the_elevated_key(
            self, auth_client, armed, monkeypatch):
        """Phase 0.5: our key cannot write the Approved stage, so the award
        editor renders disabled with a reason rather than firing a 401."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'editing the award needs an elevated XRAS key' in body
        # and it is NOT a live link to the Approved editor
        assert 'stage=Approved' not in body

    def test_an_invalid_amount_is_rejected(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '-5', 'stage': 'Requested'}).get_data(as_text=True)
        assert 'zero or more' in body

    def test_a_bad_date_range_is_rejected(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_dates_edit/EXAM0001/7',
            data={'begin_date': '2026-12-31',
                  'end_date': '2026-01-01'}).get_data(as_text=True)
        assert 'must not precede' in body

    def test_editing_the_award_is_refused_at_the_post_too(
            self, auth_client, armed, monkeypatch):
        """Belt and braces: the button is locked AND the POST refuses."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '20', 'stage': 'Approved'}).get_data(as_text=True)
        assert 'elevated XRAS key' in body

    def test_the_lever_off_refuses_an_amount_edit(self, auth_client, configured,
                                                  monkeypatch):
        """Writes off: the service refuses even a well-formed edit."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '20', 'stage': 'Requested'}).get_data(as_text=True)
        assert 'switched off' in body

    # the B2a text editors

    def test_the_attributes_form_renders_prefilled(self, auth_client, armed,
                                                   monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_attributes_form/EXAM0001').get_data(as_text=True)
        assert 'Edit attributes <span class="modal-title-meta">EXAM0001</span>' in body
        assert 'value="Turbulence at scale"' in body   # prefilled title
        assert 'name="abstract"' in body

    def test_the_action_fields_form_renders(self, auth_client, armed,
                                            monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_action_fields_form/EXAM0001/7').get_data(as_text=True)
        assert 'Edit action 7 <span class="modal-title-meta">EXAM0001</span>' in body
        assert 'name="user_comments"' in body

    def test_the_detail_modal_offers_the_text_editors(self, auth_client, armed,
                                                      monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_attributes_form/EXAM0001' in body
        assert 'xras_action_fields_form/EXAM0001/7' in body

    def test_attributes_with_no_title_is_rejected(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_attributes_edit/EXAM0001',
            data={'title': '   ', 'abstract': 'x'}).get_data(as_text=True)
        assert 'required' in body.lower()

    def test_the_lever_off_refuses_an_attributes_edit(self, auth_client,
                                                      configured, monkeypatch):
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_attributes_edit/EXAM0001',
            data={'title': 'A new title'}).get_data(as_text=True)
        assert 'switched off' in body


# per-request overrides ride ABOVE MANAGE_XRAS (create/clear = ADMIN_XRAS)

class TestOverridesRequireAdmin:
    """MANAGE_XRAS sees overrides; only ADMIN_XRAS creates or clears them."""

    ADMIN_ONLY = [
        ('post', '/allocations/xras_set_override'),
        ('post', '/allocations/xras_clear_override/mnemonic'),
        ('get', '/allocations/htmx/search-mnemonic-codes?q=ra'),
    ]

    @pytest.mark.parametrize('method,path', ADMIN_ONLY)
    def test_a_manage_only_operator_is_forbidden(self, manage_not_admin_client,
                                                 method, path):
        resp = getattr(manage_not_admin_client, method)(path)
        assert resp.status_code == 403

    def test_the_controls_show_provenance_but_gate_edit_on_admin(self, app):
        """The shared partial renders provenance to everyone, but the set/clear
        forms only when can_edit_overrides."""
        from flask import render_template

        class _MC:
            code = 'NHA'

        class _OV:
            created_by = 'op'
            comment = 'why'
            mnemonic_code = _MC()

        template = 'dashboards/allocations/partials/_xras_override_controls.html'
        ctx = dict(request_id=1, request_number='NCAR0001', blockers={'contract'},
                   overrides={'mnemonic': _OV(), 'ignore_contract': None},
                   override_return_to='readiness')
        with app.test_request_context():
            admin = render_template(template, can_edit_overrides=True, **ctx)
            viewer = render_template(template, can_edit_overrides=False, **ctx)

        # Provenance of the active mnemonic override is visible to both.
        assert 'Mnemonic set to' in admin and 'NHA' in admin
        assert 'Mnemonic set to' in viewer and 'NHA' in viewer
        # The editable parts — the contract set form and the mnemonic Clear —
        # render only for an editor.
        assert 'Ignore contract blocker' in admin and 'Ignore contract blocker' not in viewer
        assert 'Clear' in admin and 'Clear' not in viewer

    def test_admin_can_set_the_mnemonic_on_a_would_land_request(self, app):
        """The mnemonic override is offered on EVERY request an ADMIN opens, not
        only a blocked one — so a valid-but-wrong code (wchapman/NCAR4323) can be
        corrected before a projcode is minted. A non-editor still sees no box."""
        from flask import render_template

        template = 'dashboards/allocations/partials/_xras_override_controls.html'
        # No blocker, no existing override — the passing ("would land") case.
        ctx = dict(request_id=1, request_number='NCAR0001', blockers=set(),
                   overrides={'mnemonic': None, 'ignore_contract': None},
                   override_return_to='readiness')
        with app.test_request_context():
            admin = render_template(template, can_edit_overrides=True, **ctx)
            viewer = render_template(template, can_edit_overrides=False, **ctx)

        # ADMIN gets the picker plus the proactive framing; the contract form,
        # which stays blocker-gated, does NOT appear with no contract blocker.
        assert 'Set mnemonic' in admin
        assert 'override what' in admin.lower()          # the would-land framing
        assert 'Ignore contract blocker' not in admin
        # A non-editor sees no override box at all (no empty heading).
        assert 'Operator overrides' not in viewer and 'Set mnemonic' not in viewer


# the destructive lifecycle (Part C, ADMIN_XRAS)

class TestPartCIsAdminOnly:
    """The destructive verbs ride ABOVE MANAGE_XRAS: a full-editor operator is
    refused them, and the buttons render only for an ADMIN_XRAS holder."""

    DESTRUCTIVE = [
        ('get', '/allocations/xras_add_action_form/EXAM0001'),
        ('post', '/allocations/xras_request_delete/EXAM0001'),
        ('post', '/allocations/xras_request_renew/EXAM0001'),
        ('post', '/allocations/xras_add_action/EXAM0001'),
    ]

    @pytest.mark.parametrize('method,path', DESTRUCTIVE)
    def test_a_manage_only_operator_is_forbidden(self, manage_not_admin_client,
                                                 method, path):
        resp = getattr(manage_not_admin_client, method)(path)
        assert resp.status_code == 403

    def test_the_danger_zone_renders_for_an_admin(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_request_delete/EXAM0001' in body
        menu = body.split('class="dropdown-menu"', 1)[1].split('</ul>', 1)[0]
        assert 'Delete project…' in menu and 'Add action…' in menu and 'Renew…' in menu

    def test_the_danger_zone_is_hidden_from_a_manage_only_operator(
            self, manage_not_admin_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = manage_not_admin_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_request_delete' not in body
        assert 'Delete project…' not in body
        assert 'dropdown-menu' not in body.split('id="xrasModalFooter"', 1)[1]

    def test_the_add_action_form_renders_the_type_picker(self, auth_client,
                                                        armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_add_action_form/EXAM0001').get_data(as_text=True)
        assert 'Add action <span class="modal-title-meta">EXAM0001</span>' in body
        assert 'name="action_type"' in body
        assert 'Supplement' in body

    def test_an_unknown_action_type_is_rejected(self, auth_client, armed,
                                               monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_add_action/EXAM0001',
            data={'action_type': 'Nonsense'}).get_data(as_text=True)
        assert 'Must be one of' in body

    def test_an_extension_needs_the_terms(self, auth_client, armed, monkeypatch):
        from sam.manage import xras_submission as flow
        _reader(monkeypatch, payload=_detail_payload())
        monkeypatch.setattr(flow, 'request_action', lambda *a, **k: pytest.fail('filed'))
        body = auth_client.post('/allocations/xras_add_action/EXAM0001',
                                data={'action_type': 'Extension'}).get_data(as_text=True)
        assert 'terms of use' in body

    def test_an_extension_is_filed_through_the_flow_unsubmitted(self, auth_client, armed,
                                                               monkeypatch):
        from sam.manage import xras_submission as flow
        from webapp.register.eula import eula_sha
        _reader(monkeypatch, payload=_detail_payload())
        calls = []
        monkeypatch.setattr(flow, 'request_action', lambda f, draft, **kw: calls.append(
            (draft, kw)) or flow.SubmissionOutcome(1, 'populated', 'mint', None, None))
        body = auth_client.post('/allocations/xras_add_action/EXAM0001', data={
            'action_type': 'Extension', 'agree': '1', 'eula_sha': eula_sha()}).get_data(
            as_text=True)
        assert 'Added an Extension action' in body
        draft, kw = calls[0]
        assert (draft.action_type, draft.request_number) == ('Extension', 'EXAM0001')
        assert (kw['door'], kw['submit'], kw['xa_user'], kw['actor']) == \
            ('admin', False, 'pi-user', 'benkirk')

    def test_the_lever_off_refuses_a_delete(self, auth_client, configured,
                                           monkeypatch):
        """Belt and braces: the button is disabled AND the POST refuses."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_request_delete/EXAM0001').get_data(as_text=True)
        assert 'switched off' in body

    def test_delete_loops_the_whole_tree(self, auth_client, armed, monkeypatch):
        """A multi-line project deletes EVERY request line, not just lines[0]."""
        from types import SimpleNamespace

        client = _reader(monkeypatch, payload=_detail_payload())
        new_line = _detail_payload(); new_line['requestId'] = 111
        renewal = _detail_payload(); renewal['requestId'] = 222
        renewal['actions'] = [{'actionId': 88, 'actionType': 'Renewal',
                               'actionStatus': 'Approved', 'entryDate': '2027-01-02'}]
        client.get_request_family_by_number.side_effect = None
        client.get_request_family_by_number.return_value = [new_line, renewal]

        calls = []

        def fake_delete(session, *, request_number, request_id, pi_username, operator):
            calls.append(request_id)
            return SimpleNamespace(status='verified',
                                   result=SimpleNamespace(errors=None))

        monkeypatch.setattr(
            'webapp.dashboards.allocations.xras.remediation.remediation.delete_request',
            fake_delete)
        body = auth_client.post(
            '/allocations/xras_request_delete/EXAM0001').get_data(as_text=True)
        assert sorted(calls) == [111, 222]     # both lines deleted
        assert '2 request' in body             # the confirm/result names the scope


# POST validation

class TestPostValidation:

    def test_a_merge_with_no_target_is_rejected(self, auth_client, armed,
                                                monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde',
                                     'lastName': 'Host'})
        body = auth_client.post(
            '/allocations/xras_merge/ghost-user-abcde',
            data={}).get_data(as_text=True)
        assert 'Select the real XRAS identity' in body

    def test_a_merge_into_an_unresolvable_target_is_refused(
            self, auth_client, armed, monkeypatch):
        """WARNING: The API CREATES an unknown target rather than failing."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'typo'}
                                ).get_data(as_text=True)
        assert 'would CREATE it' in body

    def test_a_merge_into_itself_is_refused(self, auth_client, armed,
                                            monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'ghost-user-abcde'}
                                ).get_data(as_text=True)
        assert 'different account' in body

    def test_a_withdrawal_without_a_reason_is_rejected(self, auth_client, armed,
                                                       monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                data={'comment': '   '}).get_data(as_text=True)
        assert 'required' in body.lower()

    def test_adding_an_unknown_username_is_refused(self, auth_client, armed,
                                                   monkeypatch):
        """It would create a new XRAS identity, reconciled by default."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'nobody',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'can create a new XRAS identity' in body

    def test_adding_a_role_someone_already_holds_is_refused(
            self, auth_client, armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_person.return_value = {'username': 'ghost-user-abcde'}
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'ghost-user-abcde',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'already holds' in body

    def test_an_unknown_role_type_is_refused(self, auth_client, armed,
                                             monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'someone',
                                      'role_type': 'Reviewer'}
                                ).get_data(as_text=True)
        assert 'Must be one of' in body

    def test_removing_a_role_that_is_gone_says_so(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_remove/EXAM0001/999'
                                ).get_data(as_text=True)
        assert 'no longer on the roster' in body

    def test_the_lever_off_refuses_the_write_at_the_service(
            self, auth_client, configured, monkeypatch):
        """Belt and braces: the button is disabled AND the POST refuses."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_resubmit/EXAM0001/7'
                                ).get_data(as_text=True)
        assert 'switched off' in body


# review fixes (PR #460 follow-up)

class TestThePreflightDegradesHonestly:
    """The resubmit modal's preflight has three non-happy paths, and each
    must render — not 500, and not masquerade as a different failure."""

    def _admin(self, monkeypatch, **script):
        client = MagicMock(**script)
        monkeypatch.setattr(
            'sam.integration.xras_api.admin_client'
            '.XrasAdminClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        return client

    def test_a_role_less_roster_renders_the_unvalidated_note(
            self, auth_client, armed, monkeypatch):
        """No xa_user means no preflight ran — the guard skips seeding, and
        an unseeded key was jinja2.Undefined, for which `is none` is False
        and the next attribute access a 500 with an empty modal."""
        payload = _payload()
        payload['roles'] = []
        _reader(monkeypatch, payload=payload)
        response = auth_client.get('/allocations/xras_resubmit_form/EXAM0001/7')
        assert response.status_code == 200
        assert 'could not be asked to pre-check' in response.get_data(as_text=True)

    def test_a_preflight_401_renders_the_refusal_not_an_outage(
            self, auth_client, armed, monkeypatch):
        """WARNING: XrasWriteRejected subclasses XrasSourceUnavailable, so the
        outage catch used to swallow it — telling the operator to 'retry
        later' about a refusal a retry can never fix."""
        from sam.integration.xras_api.base import XrasWriteRejected

        _reader(monkeypatch)
        admin = self._admin(monkeypatch)
        admin.validate_action.side_effect = XrasWriteRejected(
            'pi-user holds no role on this request', status=401)

        body = auth_client.get(
            '/allocations/xras_resubmit_form/EXAM0001/7').get_data(as_text=True)
        assert 'does not validate' in body
        assert 'holds no role' in body
        assert 'not answering' not in body


class TestAPostDuringAnOutageDegradesInline:
    """exception_map wraps perform() only; the live reads in clean() needed
    their own wrap or an outage there was a 500 htmx never swaps."""

    def test_a_withdraw_post_renders_the_outage_inline(self, auth_client,
                                                       armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        response = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                    data={'comment': 'stale award'})
        assert response.status_code == 200
        assert 'could not be reached' in response.get_data(as_text=True)

    def test_a_role_add_post_renders_the_outage_inline(self, auth_client,
                                                       armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        response = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                    data={'username': 'somebody',
                                          'role_type': 'User'})
        assert response.status_code == 200
        assert 'could not be reached' in response.get_data(as_text=True)


class TestARejectionCarriesXrasReasons:
    """A 400 carries XRAS's own errors[] — the most actionable thing about a
    rejection — and they must reach the operator, not only the audit row."""

    def test_the_reasons_render_in_the_error_panel(self, auth_client, armed,
                                                   monkeypatch):
        from sam.integration.xras_api.base import XrasWriteRejected
        from sam.manage import xras_remediation as remediation
        from sam.manage.xras_remediation import RemediationOutcome

        _reader(monkeypatch)
        rejection = XrasWriteRejected(
            'XRAS validation failed for action 7 as pi-user', status=400,
            errors=['Budget exceeds the opportunity limit',
                    'End date precedes the begin date'])
        monkeypatch.setattr(
            remediation, 'withdraw_action',
            lambda *a, **k: RemediationOutcome(5, status='rejected',
                                               error=str(rejection),
                                               result=rejection))

        body = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                data={'comment': 'why'}).get_data(as_text=True)
        assert 'XRAS refused this' in body
        assert 'Budget exceeds the opportunity limit' in body
        assert 'End date precedes the begin date' in body


class TestTheMergeOverrideIsReachable:
    """`required` on the candidate radios let native constraint validation
    block the free-text override — the documented path for 'none of these is
    right' — whenever radios render (two or more candidates; a single match is
    the confirmed target with no radio and no override)."""

    def _form(self, auth_client, monkeypatch):
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid'},
                candidates=[{'username': 'ghost', 'firstName': 'G',
                             'lastName': 'Host', 'email': 'g@example.invalid',
                             'organization': 'Example U'},
                            {'username': 'ghosty', 'firstName': 'G',
                             'lastName': 'Host', 'email': 'other@example.invalid',
                             'organization': 'NCAR'}])
        return auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde'
        ).get_data(as_text=True)

    def test_the_radios_are_not_marked_required(self, auth_client, armed,
                                                monkeypatch):
        import re
        body = self._form(auth_client, monkeypatch)
        radios = re.findall(r'<input[^>]*name="candidate"[^>]*>', body)
        assert radios, 'the candidate radios must still render'
        assert all('required' not in tag for tag in radios)

    def test_no_radio_is_preselected(self, auth_client, armed, monkeypatch):
        """The exactly-one guarantee moved to the schema; the no-default
        guarantee stays in the template."""
        import re
        body = self._form(auth_client, monkeypatch)
        radios = re.findall(r'<input[^>]*name="candidate"[^>]*>', body)
        assert all('checked' not in tag for tag in radios)


class TestSelfMergeIsRefusedCaseInsensitively:
    """XRAS matches usernames case-insensitively, so a case-variant of the
    source is the same identity and would be a self-merge."""

    def test_a_case_variant_target_is_refused(self, auth_client, armed,
                                              monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'Ghost-USER-Abcde'}
                                ).get_data(as_text=True)
        assert 'different account' in body


class TestTheServiceGetsAFactory:
    """The audit row is written on a session the service opens itself.

    Passing an already-open ``Session`` where the service expects a factory
    does not fail the request: ``_open_event`` logs and returns None, the
    write proceeds, and the irreversible act leaves no audit row. That is
    exactly what the first production merge did.
    """

    def test_the_merge_route_hands_the_service_a_callable(
            self, auth_client, armed, monkeypatch):
        from sqlalchemy.orm import Session

        from sam.manage import xras_remediation
        from sam.manage.xras_remediation import RemediationOutcome

        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        seen = {}

        def spy(session_factory, **kwargs):
            seen['factory'] = session_factory
            return RemediationOutcome(event_id=None, status='error',
                                      error='spy')

        monkeypatch.setattr(xras_remediation, 'merge_placeholder', spy)
        auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                         data={'target_username': 'real'})

        assert callable(seen['factory']), \
            'the route passed a Session, not a factory; no audit row is written'
        session = seen['factory']()
        try:
            assert isinstance(session, Session)
        finally:
            session.close()
