"""``sam.manage.xras_submission``: the audited steps that file a user's XRAS action."""

from __future__ import annotations

import itertools
import os
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from xras_commit_helpers import tracking_factory, write_result

from sam.integration.xras import XrasRemediationEvent
from sam.integration.xras_api.base import XrasSourceUnavailable, XrasWriteRejected
from sam.integration.xras_submission import XrasSubmission
from sam.manage import xras_submission as flow
from sam.manage.xras_submission import (
    ActionRequest,
    ResourceAmount,
    SamRefs,
    SubmissionDocument,
    SubmissionRefused,
)

EULA = ('a' * 40, datetime(2026, 10, 29, 9, 0))

#: Committed rows are shared across xdist workers and action_id is unique: fresh ids per test.
_IDS = itertools.count(900_000_000 + (os.getpid() % 10_000) * 1_000)


def _ok(operation, verified=True, **extra):
    return write_result(operation, verified=verified, extra=extra)


@pytest.fixture
def factory(engine):
    """A real session factory; the rows it commits are deleted at teardown by id."""
    with tracking_factory(engine, XrasSubmission, XrasRemediationEvent) as make:
        yield make


@pytest.fixture(autouse=True)
def armed(monkeypatch):
    monkeypatch.setattr(flow, 'submit_armed', lambda *a, **k: True)
    monkeypatch.setattr(flow, '_patch_card', lambda *a, **k: None)


def _row(factory, sid):
    with factory() as s:
        row = s.get(XrasSubmission, sid)
        s.expunge(row)
        return row


def _event_ops(factory, row):
    with factory() as s:
        return {step: s.get(XrasRemediationEvent, i).operation
                for step, i in row.steps.items()}


def _extension(**kw):
    kw.setdefault('request_id', next(_IDS))
    return ActionRequest(action_type='Extension', request_number='UHSS0001',
                         user_comments='more time', dates=(None, date(2027, 9, 30)),
                         sam=SamRefs(project_id=11), **kw)


def _client(action_id=None):
    client = MagicMock()
    client.action_id = action_id or next(_IDS)
    client.add_action.return_value = _ok('add_action', action_id=client.action_id)
    client.set_action_dates.return_value = _ok('set_action_dates')
    client.submit_action.return_value = _ok('submit_action')
    client.delete_action.return_value = _ok('delete_action')
    client.delete_request.return_value = _ok('delete_request')
    return client


class TestTheExtension:

    def test_two_writes_then_submit_each_on_its_own_audit_row(self, factory):
        client = _client()
        draft = _extension()
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)

        assert out.succeeded and out.state == 'submitted'
        row = _row(factory, out.submission_id)
        assert (row.action_id, row.request_id, row.project_id) == \
            (client.action_id, draft.request_id, 11)
        assert row.eula_sha == EULA[0] and row.submitted_time is not None
        assert _event_ops(factory, row) == {'mint': 'add_action',
                                            'dates': 'set_action_dates',
                                            'submit': 'submit_action'}
        client.add_action.assert_called_once_with(
            draft.request_id, 'Extension', request_number='UHSS0001', xa_user='pi1',
            user_comments='more time')
        args = client.set_action_dates.call_args
        assert args.args == (draft.request_id, client.action_id, None, date(2027, 9, 30))

    def test_the_row_records_the_pis_and_the_admins_user_ids(self, factory):
        from sam.core.users import User
        from sam.projects.projects import Project
        with factory() as s:
            project = (s.query(Project).filter(Project.project_admin_user_id.isnot(None))
                       .order_by(Project.project_id).first())
            pi = s.get(User, project.project_lead_user_id)
            pid, username = project.project_id, pi.username
            expected = (pi.user_id, project.project_admin_user_id)
        draft = ActionRequest(action_type='Extension', request_number='UHSS0001',
                              request_id=next(_IDS), dates=(None, date(2027, 9, 30)),
                              sam=SamRefs(project_id=pid))
        out = flow.request_action(factory, draft, actor=username, xa_user=username,
                                  eula=EULA, client=_client())
        row = _row(factory, out.submission_id)
        assert (row.user_id, row.admin_user_id) == expected

    def test_submit_false_stops_at_populated(self, factory):
        client = _client()
        out = flow.request_action(factory, _extension(), actor='op', xa_user='pi1',
                                  eula=EULA, submit=False, door='admin', client=client)
        assert out.state == 'populated'
        client.submit_action.assert_not_called()
        assert _row(factory, out.submission_id).created_by == 'op'


class TestAStepThatFails:

    def test_resume_honors_the_rows_submit(self, factory):
        """An operator's Add-action opens the row with ``submit=False``; the resume that
        completes it stops before the submit too, and an explicit ``submit=`` overrides."""
        client = _client()
        client.set_action_dates.side_effect = XrasWriteRejected('bad date', status=400,
                                                                errors=['End date'])
        out = flow.request_action(factory, _extension(), actor='op1', xa_user='pi1',
                                  eula=EULA, submit=False, door='admin', client=client)
        assert out.step == 'dates'
        client.set_action_dates.side_effect = None
        again = flow.resume_submission(factory, out.submission_id, actor='op1', door='admin',
                                       client=client)
        assert again.succeeded and again.state == 'populated'
        assert client.submit_action.call_count == 0
        last = flow.resume_submission(factory, out.submission_id, actor='op1', door='admin',
                                      submit=True, client=client)
        assert last.state == 'submitted' and client.submit_action.call_count == 1

    def test_the_lever_off_refuses_the_pi_doors_abort_only(self, factory, monkeypatch):
        client = _client()
        client.add_action.return_value = _ok('add_action', verified=False,
                                             action_id=client.action_id)
        out = flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        monkeypatch.setattr(flow, 'submit_armed', lambda *a, **k: False)
        with pytest.raises(SubmissionRefused, match='not enabled'):
            flow.abort_submission(factory, out.submission_id, actor='pi1', client=client)
        assert client.delete_action.call_count == 0
        ended = flow.abort_submission(factory, out.submission_id, actor='op1', door='admin',
                                      client=client)
        assert ended.state == 'aborted' and client.delete_action.call_count == 1

    def test_a_rejection_stops_and_keeps_the_state(self, factory):
        client = _client()
        client.submit_action.side_effect = XrasWriteRejected(
            'validation failed', status=400, errors=['End date is required'])
        out = flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)

        assert not out.succeeded and out.step == 'submit'
        row = _row(factory, out.submission_id)
        assert row.state == 'populated'
        assert 'End date is required' in row.last_error
        client.delete_action.assert_not_called()      # no automatic abort

    def test_resume_skips_the_verified_steps(self, factory):
        client = _client()
        client.submit_action.side_effect = XrasSourceUnavailable('timeout')
        out = flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        assert out.step == 'submit'

        client.submit_action.side_effect = None
        again = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                       client=client)
        assert again.state == 'submitted'
        assert client.add_action.call_count == 1
        assert client.set_action_dates.call_count == 1
        assert _row(factory, out.submission_id).last_error is None

    def test_an_add_that_may_have_landed_is_never_resent(self, factory):
        client = _client()
        client.add_action.return_value = _ok('add_action', verified=None,
                                             action_id=client.action_id)
        out = flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        assert out.step == 'mint' and out.state == 'draft'
        assert _row(factory, out.submission_id).action_id == client.action_id  # for the abort

        again = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                       client=client)
        assert not again.succeeded and 'abort rather than resume' in again.error
        assert client.add_action.call_count == 1

    def test_a_step_whose_audit_row_was_lost_is_never_resent(self, factory):
        """``_open_event`` returning ``None`` records the step with no id; the write may
        have landed, so a resume stops there as it does for an unverified add."""
        client = _client()
        client.set_action_dates.return_value = _ok('set_action_dates', verified=False)
        out = flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        assert out.step == 'dates'
        with factory() as s:
            s.get(XrasSubmission, out.submission_id).record_step('dates', None)
            s.commit()

        again = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                       client=client)
        assert not again.succeeded and 'dates may have reached XRAS (unknown)' in again.error
        assert client.set_action_dates.call_count == 1

    def test_abort_deletes_the_action(self, factory):
        client = _client()
        client.submit_action.side_effect = XrasWriteRejected('no', status=400)
        draft = _extension()
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        done = flow.abort_submission(factory, out.submission_id, actor='pi1',
                                     client=client)
        assert done.state == 'aborted'
        client.delete_action.assert_called_once_with(draft.request_id, client.action_id,
                                                     xa_user='pi1')
        assert _row(factory, out.submission_id).aborted_time is not None


class TestTheNew:

    def test_create_then_the_pi_role_then_abort_deletes_the_line(self, factory):
        client = _client()
        rid, aid = next(_IDS), next(_IDS)
        client.create_request.return_value = _ok(
            'create_request', request_id=rid, action_id=aid, response={'requestId': rid})
        client.add_role.return_value = _ok('add_role')
        client.update_request_attributes.return_value = _ok('update_attributes')
        draft = ActionRequest(action_type='New', opportunity_id=532220, title='T',
                              abstract='A', resources=(ResourceAmount(500055, 5e5),),
                              admin_username='adm1')
        client.update_resource_amount.return_value = _ok('update_resource_amount')
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1',
                                  eula=EULA, submit=False, client=client)

        assert out.state == 'populated'
        row = _row(factory, out.submission_id)
        assert (row.request_id, row.action_id, row.request_type) == (rid, aid, 'New')
        assert row.snapshot_dict == {'requestId': rid}
        roles = [c.args[1] for c in client.add_role.call_args_list]
        assert roles == ['PI', 'Allocation Manager']

        flow.abort_submission(factory, out.submission_id, actor='pi1', client=client)
        client.delete_request.assert_called_once_with(rid, request_number=None,
                                                      xa_user='pi1')
        client.delete_action.assert_not_called()

    def test_the_roles_and_the_grant_read_the_unnumbered_draft_by_id(self, factory):
        client = _client()
        rid, aid = next(_IDS), next(_IDS)
        client.create_request.return_value = _ok(
            'create_request', request_id=rid, action_id=aid, response={'requestId': rid})
        for verb, op in (('add_role', 'add_role'), ('update_request_attributes',
                                                    'update_attributes'),
                         ('set_fos', 'set_fos'), ('add_grant', 'add_grant')):
            getattr(client, verb).return_value = _ok(op)
        draft = ActionRequest(action_type='New', opportunity_id=532220, title='T',
                              abstract='A', fos_type_id=500003,
                              grants=({'grantNumber': '2345678', 'fundingAgencyId': 1},))
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1', eula=EULA,
                                  client=client)
        assert out.state == 'submitted'
        # every step after the mint: the PI role, attributes, fos, grant, submit
        assert client.reading_draft.call_count == 5
        assert {c.args for c in client.reading_draft.call_args_list} == {(rid, 'pi1')}
        attrs = client.update_request_attributes.call_args.kwargs
        assert attrs['isSupportedByGrants'] == 'true' and attrs['request_number'] is None


class TestDocuments:

    def test_the_payload_keeps_no_bytes_and_resume_asks_for_them(self, factory):
        client = _client()
        client.upload_document.side_effect = XrasSourceUnavailable('timeout')
        doc = SubmissionDocument('Main Document', 'plan.pdf', b'%PDF-1.7 body')
        draft = ActionRequest(action_type='Supplement', request_id=next(_IDS), request_number='X1',
                              documents=(doc,))
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        stored = _row(factory, out.submission_id).payload_dict['documents'][0]
        assert stored['bytes'] == 13 and 'content' not in stored

        again = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                       client=client)
        assert 'may have reached XRAS' in again.error  # an upload is an add

    def test_a_document_never_sent_must_be_attached_again(self, factory):
        client = _client()
        client.add_action.return_value = _ok('add_action', verified=False,
                                             action_id=client.action_id)
        doc = SubmissionDocument('Main Document', 'plan.pdf', b'%PDF-1.7 body')
        draft = ActionRequest(action_type='Supplement', request_id=next(_IDS), request_number='X1',
                              documents=(doc,))
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1',
                                  eula=EULA, client=client)
        with factory() as s:
            row = s.get(XrasSubmission, out.submission_id)
            row.advance('created')
            s.commit()
        again = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                       client=client)
        assert 'attach plan.pdf again' in again.error
        client.upload_document.assert_not_called()

        client.add_action.return_value = _ok('add_action', action_id=client.action_id)
        client.upload_document.return_value = _ok('upload_document')
        done = flow.resume_submission(factory, out.submission_id, actor='pi1',
                                      documents=(doc,), client=client)
        assert done.succeeded and done.state == 'submitted'
        assert client.upload_document.call_args.kwargs['content'] == b'%PDF-1.7 body'


class TestRefusals:

    def test_the_lever_off_refuses_the_pi_door_before_any_write(self, factory, monkeypatch):
        monkeypatch.setattr(flow, 'submit_armed', lambda *a, **k: False)
        client = _client()
        with pytest.raises(SubmissionRefused, match='not enabled'):
            flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                eula=EULA, client=client)
        client.add_action.assert_not_called()

    def test_no_agreement_no_row(self, factory):
        with pytest.raises(SubmissionRefused, match='agreement'):
            flow.request_action(factory, _extension(), actor='pi1', xa_user='pi1',
                                eula=(None, None), client=_client())

    def test_a_second_open_row_on_the_line_is_refused(self, factory):
        client = _client()
        client.submit_action.side_effect = XrasWriteRejected('no', status=400)
        rid = next(_IDS)
        flow.request_action(factory, _extension(request_id=rid), actor='pi1',
                            xa_user='pi1', eula=EULA, client=client)
        with pytest.raises(SubmissionRefused, match='already in progress'):
            flow.request_action(factory, _extension(request_id=rid), actor='pi1',
                                xa_user='pi1', eula=EULA, client=client)


class TestThePayload:

    def test_round_trip(self):
        draft = ActionRequest(action_type='Renewal', request_id=3, request_number='R1',
                              dates=(date(2027, 1, 1), date(2027, 12, 31)),
                              grants=({'grantNumber': '2345678'},),
                              resources=(ResourceAmount(1, 2.0, 'why'),),
                              sam=SamRefs(project_id=4, contract_id=5))
        assert ActionRequest.from_payload(draft.to_payload()) == draft


class TestTheRenewal:

    def test_renew_then_populate_by_id_on_the_new_line(self, factory):
        client = _client()
        old, rid, aid = next(_IDS), next(_IDS), next(_IDS)
        client.renew_request.return_value = _ok(
            'renew_request', renewal_request_id=rid, action_id=aid, response={'requestId': rid})
        for verb, op in (('update_request_attributes', 'update_attributes'),
                         ('update_resource_amount', 'update_resource_amount'),
                         ('set_opportunity_attributes', 'set_opp_attributes'),
                         ('upload_document', 'upload_document')):
            getattr(client, verb).return_value = _ok(op)
        draft = ActionRequest(
            action_type='Renewal', request_number='UHSS0002', request_id=old,
            opportunity_id=532221, abstract='Year two.', eua_attribute_id=539638,
            resources=(ResourceAmount(530902, 1000.0),),
            dates=(date(2027, 11, 1), date(2028, 10, 31)),
            documents=(SubmissionDocument('Advisor_Letter', 'letter.pdf', b'%PDF-1.7 x'),))
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1', eula=EULA,
                                  client=client)

        assert out.succeeded and out.state == 'submitted'
        row = _row(factory, out.submission_id)
        assert (row.request_id, row.action_id, row.request_type) == (rid, aid, 'Renewal')
        client.renew_request.assert_called_once_with(old, request_number='UHSS0002',
                                                     xa_user='pi1', opportunity_id=532221)
        assert _event_ops(factory, row) == {
            'mint': 'renew_request', 'attributes': 'update_attributes',
            'resource:530902': 'update_resource_amount', 'dates': 'set_action_dates',
            'opp_attributes': 'set_opp_attributes', 'document:letter.pdf': 'upload_document',
            'submit': 'submit_action'}
        scopes = {c.args for c in client.reading_draft.call_args_list}
        assert scopes == {(rid, 'pi1')} and client.reading_draft.call_count == 6

    @pytest.mark.parametrize('failure', [XrasWriteRejected('no renewal here', status=400),
                                         XrasSourceUnavailable('timeout')])
    def test_a_renew_that_never_landed_leaves_the_original_line_on_discard(self, factory,
                                                                           failure):
        client = _client()
        client.renew_request.side_effect = failure
        old = next(_IDS)
        draft = ActionRequest(action_type='Renewal', request_number='UHSS0002', request_id=old,
                              opportunity_id=532221, abstract='Year two.')
        out = flow.request_action(factory, draft, actor='pi1', xa_user='pi1', eula=EULA,
                                  client=client)
        assert out.step == 'mint' and out.state == 'draft'
        assert _row(factory, out.submission_id).request_id == old   # the line it renews

        done = flow.abort_submission(factory, out.submission_id, actor='pi1', client=client)
        assert done.state == 'aborted'
        client.delete_request.assert_not_called()
        client.delete_action.assert_not_called()
