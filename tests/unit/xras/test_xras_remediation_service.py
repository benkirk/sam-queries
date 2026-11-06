"""``sam.manage.xras_remediation`` — the service that ties client, audit and card.

House convention puts write happy-paths at the service layer, and the
properties worth pinning here are the ones that only show up when something
goes wrong:

* the audit row survives a client explosion, because it is committed **before**
  the write and on a session no caller can roll back;
* a merge invalidates both people-cache entries, or the card keeps serving the
  placeholder it just merged away;
* the post-write snapshot patch produces an entry **identical in shape** to the
  sweep's, because they are the same function;
* a failed patch is not a failed write.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from xras_commit_helpers import tracking_factory, write_result
from factories import make_xras_remediation_event  # noqa: F401  (fixture parity)

from sam.integration.xras import XrasRemediationEvent
from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.base import (
    XrasSourceUnavailable,
    XrasWriteRejected,
)
from sam.manage import xras_remediation as service
from sam.queries.xras_remediations import (
    list_remediation_events,
    remediation_summary,
)
from sam.queries.xras_requests import request_index_entry



def _payload(number='EXAM0001', status='Approved', action_status='Approved'):
    return {
        'requestId': 900001, 'requestNumber': number, 'requestStatus': status,
        'requestType': 'New', 'opportunityId': 5, 'opportunity_name': 'Small',
        'submitDate': '2026-01-01T00:00:00Z',
        'roles': [{'person': {'username': 'pi-user', 'firstName': 'P',
                              'lastName': 'Eye', 'isReconciled': True},
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]},
                  {'person': {'username': 'ghost-user-abcde', 'firstName': 'G',
                              'lastName': 'Host', 'isReconciled': True},
                   'roles': [{'roleId': 2, 'role': 'User', 'roleTypeId': 19}]}],
        'actions': [{'actionId': 7, 'actionType': 'Supplement',
                     'actionStatus': action_status}],
    }


def _result(operation='withdraw_action', verified=True, **kw):
    kw.setdefault('before', 'Approved')
    kw.setdefault('after', 'Incomplete')
    return write_result(operation, method='DELETE', verified=verified, **kw)


@pytest.fixture
def factory(engine):
    """A real session factory: the service commits its own sessions, so the shared test DB
    must be left as the blob had it (`make regen-lfs-blob` dumps it)."""
    with tracking_factory(engine, XrasRemediationEvent) as make:
        yield make


@pytest.fixture(autouse=True)
def _cache(monkeypatch):
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    xras_cache._CACHE.reset_for_tests(disabled=False)
    yield
    xras_cache._CACHE.reset_for_tests(disabled=False)


@pytest.fixture
def published():
    """A published index holding one request, as the sweep would leave it."""
    entry = request_index_entry(_payload(), pending_push=True)
    xras_cache.store_requests_index({'generated_at': datetime.now(),
                                     'rows': [entry]})
    return entry


def _rows(session, **kw):
    return list_remediation_events(session, **kw)


# the audit row outlives the write

class TestTheAuditRowSurvives:

    def test_a_client_explosion_still_leaves_a_row(self, factory, session):
        """Committed before dispatch, on its own session — that is the point."""
        client = MagicMock()
        client.withdraw_action.side_effect = XrasSourceUnavailable('XRAS down')

        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', comment='stale',
            client=client)

        assert outcome.status == 'error'
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert row is not None
        assert row.operation == 'withdraw_action'
        assert row.outcome_reason == 'XRAS down'

    def test_a_rejection_is_recorded_as_rejected_with_its_status(
            self, factory, session):
        client = MagicMock()
        client.withdraw_action.side_effect = XrasWriteRejected(
            'not a role holder', status=401)

        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='nobody', operator='benkirk', comment='x', client=client)

        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.status, row.http_status) == ('rejected', 401)

    def test_a_rejection_records_xras_own_validation_errors(
            self, factory, session):
        """WARNING: The list is what an operator revisits, and it does not fit in
        `outcome_reason` (VARCHAR(255)) — so it goes to `after_state`, which is
        TEXT and utf8mb4. Previously `exc.errors` was dropped entirely and the
        row said only "validation failed"."""
        client = MagicMock()
        client.submit_action.side_effect = XrasWriteRejected(
            'XRAS validation failed for action 7 as pi-user', status=400,
            errors=['Title is a required field',
                    'A PI CV is required for each PI',
                    'The Progress Report upload is required'])

        outcome = service.resubmit_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', client=client)

        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.status, row.http_status) == ('rejected', 400)
        assert 'A PI CV is required' in row.after_state
        assert 'Progress Report' in row.after_state

    def test_a_rejection_with_no_error_list_leaves_after_state_empty(
            self, factory, session):
        """A 401 carries no `errors[]` — an empty JSON husk would be noise."""
        client = MagicMock()
        client.withdraw_action.side_effect = XrasWriteRejected(
            'not a role holder', status=401)
        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='nobody', operator='benkirk', comment='x', client=client)
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert row.after_state is None
        assert row.outcome_reason == 'not a role holder'

    def test_the_operator_and_the_impersonation_are_both_recorded(
            self, factory, session):
        client = MagicMock()
        client.withdraw_action.return_value = _result()

        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', comment='stale',
            client=client)

        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.created_by, row.xa_user) == ('benkirk', 'pi-user')

    def test_the_before_state_is_recorded_for_action_ops_too(
            self, factory, session):
        """Not merge-specific: the prior status is what makes a withdraw row
        readable a year later."""
        client = MagicMock()
        client.withdraw_action.return_value = _result()
        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', comment='x',
            client=client)
        assert session.get(XrasRemediationEvent,
                           outcome.event_id).before_state == 'Approved'

    def test_an_unverified_write_is_not_reported_as_success(
            self, factory, session):
        client = MagicMock()
        client.withdraw_action.return_value = _result(verified=False)

        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', comment='x', client=client)

        assert outcome.succeeded is False
        assert session.get(XrasRemediationEvent,
                           outcome.event_id).status == 'unverified'

    def test_a_broken_audit_table_does_not_block_the_remediation(self, session):
        """A logging outage must not stop an operator fixing production."""
        def exploding_factory():
            raise RuntimeError('audit DB unreachable')

        client = MagicMock()
        client.withdraw_action.return_value = _result()
        outcome = service.withdraw_action(
            exploding_factory, request_number='EXAM0001', request_id=900001,
            action_id=7, pi_username='pi-user', operator='benkirk',
            comment='x', client=client)

        assert outcome.event_id is None
        assert outcome.succeeded is True


# merge

class TestMerge:

    def test_both_cache_entries_are_invalidated(self, factory, monkeypatch):
        """Source because it is gone; target because its roles changed."""
        seen = []
        monkeypatch.setattr(xras_cache, 'invalidate_person', seen.append)
        monkeypatch.setattr(service, '_patch_requests_naming',
                            lambda _u, **kw: True)

        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')

        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='real', operator='benkirk',
                                  client=client)
        assert seen == ['ghost-user-abcde', 'real']

    def test_nothing_is_invalidated_when_the_merge_did_not_verify(
            self, factory, monkeypatch):
        seen = []
        monkeypatch.setattr(xras_cache, 'invalidate_person', seen.append)
        client = MagicMock()
        client.merge_person.return_value = _result('merge_person', verified=False)

        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='real', operator='benkirk',
                                  client=client)
        assert seen == []

    def test_the_pre_merge_person_sheet_is_recorded(self, factory, session,
                                                    monkeypatch):
        """WARNING: The reason `before_state` exists at all.

        Merge does not copy person detail, so `residenceCountry` — which the
        inbound wire never carries either — exists nowhere SAM can reach once
        the source is deleted. This column is the only copy.

        Regression: the service captured it in the client result and never
        wrote it, leaving the column permanently NULL. Caught by a local DDL
        smoke on 2026-08-21, not by any test — hence this one.
        """
        monkeypatch.setattr(service, '_patch_requests_naming',
                            lambda _u, **kw: True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)

        sheet = {'username': 'ghost-user-abcde', 'residenceCountry': 'Canada',
                 'organization': 'Example University', 'phone': '555'}
        client = MagicMock()
        client.merge_person.return_value = _result(
            'merge_person', before={'source': sheet, 'target': None})

        outcome = service.merge_placeholder(
            factory, source_username='ghost-user-abcde',
            target_username='real', operator='benkirk', client=client)

        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert row.before_state is not None, \
            'the pre-merge capture never reached the audit row'
        assert 'Canada' in row.before_state
        assert 'Example University' in row.before_state

    def test_both_usernames_are_recorded(self, factory, session):
        client = MagicMock()
        client.merge_person.return_value = _result('merge_person',
                                                   before={'source': {'x': 1}})
        outcome = service.merge_placeholder(
            factory, source_username='ghost-user-abcde', target_username='real',
            operator='benkirk', client=client)
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.username, row.target_username) == ('ghost-user-abcde', 'real')
        assert row.xa_user is None, 'merge is user-agnostic'

    def test_it_refreshes_every_request_naming_the_placeholder(
            self, factory, published, monkeypatch):
        refreshed = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: refreshed.append(n) or True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)

        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')
        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='real', operator='benkirk',
                                  client=client)
        assert refreshed == ['EXAM0001']

    def test_a_merged_placeholder_pending_row_is_dropped(
            self, factory, monkeypatch):
        """The Pending Users card renders the cached pending half, so a merged
        placeholder must lose its row now, not at the next sweep."""
        monkeypatch.setattr(service, '_patch_requests_naming',
                            lambda _u, **kw: True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)
        xras_cache.store_pending_worklist(
            {'generated_at': datetime.now(), 'window_days': 14,
             'rows': [{'username': 'Ghost-User-Abcde'}, {'username': 'other'}]})

        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')
        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='real', operator='benkirk',
                                  client=client)

        remaining = [r['username']
                     for r in xras_cache.load_pending_worklist()['rows']]
        assert remaining == ['other']       # matched case-insensitively

    def test_a_failed_pending_drop_does_not_fail_the_merge(
            self, factory, monkeypatch):
        monkeypatch.setattr(service, '_patch_requests_naming',
                            lambda _u, **kw: True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)
        monkeypatch.setattr(
            xras_cache, 'drop_pending_worklist_row',
            lambda _u: (_ for _ in ()).throw(RuntimeError('cache down')))

        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')
        outcome = service.merge_placeholder(
            factory, source_username='ghost-user-abcde',
            target_username='real', operator='benkirk', client=client)
        assert outcome.status == 'verified'


class TestMergeDismissesTheSweepRows:
    """XRAS_POST_AUTOMATION.md D item 6: fulfillment matches by email, so a merge
    into an account holding another address would leave the row open for good."""

    def test_the_placeholders_open_rows_are_dismissed(self, factory, engine, monkeypatch):
        from sqlalchemy.orm import Session

        from sam.core.account_requests import AccountRequest
        monkeypatch.setattr(service, '_patch_requests_naming', lambda _u, **kw: True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)
        with Session(engine) as s:
            row = AccountRequest.create(s, email='zz.merge.dismiss@example.invalid',
                                        first_name='G', last_name='Host',
                                        purpose='submission', created_by='task:xras_sweep',
                                        xras_username='Ghost-User-Abcde')
            s.commit()
            rid = row.account_request_id
        try:
            client = MagicMock()
            client.merge_person.return_value = _result('merge_person')
            service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                      target_username='real', operator='benkirk',
                                      client=client)
            with Session(engine) as s:
                row = s.get(AccountRequest, rid)
                assert (row.state, row.closed_by) == ('dismissed', 'benkirk')
                assert row.closed_reason == 'XRAS placeholder merged into real'
                assert row.merged_at is not None
        finally:
            with Session(engine) as s:
                s.query(AccountRequest).filter_by(account_request_id=rid).delete()
                s.commit()


class TestTheVisitorMerge:
    """XRAS_SUBMISSION_SPRINT2.md 3b: a /request placeholder merges into its new account."""

    @pytest.fixture
    def fulfilled(self, engine, monkeypatch):
        """A fulfilled visitor row and its submission, committed: ``(placeholder, user, line)``."""
        from sqlalchemy.orm import Session

        from factories import next_seq
        from sam.core.account_requests import AccountRequest
        from sam.core.users import User
        from sam.integration.xras_submission import XrasSubmission
        monkeypatch.setattr(service, '_patch_requests_naming', lambda _u, **kw: True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)
        n = next_seq('9')
        placeholder, line = f'samr{n}-user-abc123', int(n)
        with Session(engine) as s:
            user = s.query(User).filter(User.is_active).order_by(User.user_id).first()
            row = AccountRequest.create(
                s, email=f'zz.visitor.{n}@example.invalid', first_name='Vis', last_name='Itor',
                purpose='submission', created_by='self', verified_by='self',
                xras_username=placeholder)
            row.fulfill(user)
            sub = XrasSubmission.create(
                s, action_type='New', created_by=f'registration:{row.account_request_id}',
                xa_user=placeholder, eula_sha='a' * 40, eula_accepted_at=datetime.now(),
                payload={}, request_id=line, registration_id=row.account_request_id)
            s.commit()
            ids = (row.account_request_id, sub.xras_submission_id)
            target = (user.username, user.user_id)
        yield placeholder, target, line, ids
        with Session(engine) as s:
            s.query(XrasSubmission).filter_by(xras_submission_id=ids[1]).delete()
            s.query(AccountRequest).filter_by(account_request_id=ids[0]).delete()
            s.commit()

    def test_it_merges_repoints_and_closes_the_row(self, factory, engine, fulfilled):
        from sqlalchemy.orm import Session

        from sam.core.account_requests import AccountRequest
        from sam.integration.xras_submission import XrasSubmission
        placeholder, (username, user_id), line, (rid, sid) = fulfilled
        client = MagicMock()
        client.get_person.return_value = {'username': username}
        client.merge_person.return_value = _result('merge_person')
        out = service.merge_fulfilled_visitors(factory, operator='benkirk', client=client)
        assert (placeholder, username, [line]) in out['merged']
        client.merge_person.assert_any_call(placeholder, username)
        with Session(engine) as s:
            assert (s.get(XrasSubmission, sid).xa_user, s.get(XrasSubmission, sid).user_id) == \
                (username, user_id)
            row = s.get(AccountRequest, rid)
            assert row.merged_at is not None and row.is_fulfilled
        again = service.merge_fulfilled_visitors(factory, operator='benkirk', client=client)
        assert all(m[0] != placeholder for m in again['merged'])

    def test_an_account_xras_does_not_know_yet_waits(self, factory, fulfilled):
        placeholder = fulfilled[0]
        client = MagicMock()
        client.get_person.return_value = None
        out = service.merge_fulfilled_visitors(factory, operator='benkirk', client=client)
        assert out['waiting'] >= 1 and out['merged'] == []
        client.merge_person.assert_not_called()


class TestDropPendingWorklistRow:
    """The cache primitive behind the merge's coherence patch."""

    def test_no_snapshot_is_not_an_error(self):
        assert xras_cache.drop_pending_worklist_row('ghost') is False

    def test_an_absent_username_is_not_an_error(self):
        xras_cache.store_pending_worklist(
            {'rows': [{'username': 'someone'}]})
        assert xras_cache.drop_pending_worklist_row('nobody') is False

    def test_it_drops_the_matching_row_case_insensitively(self):
        xras_cache.store_pending_worklist(
            {'rows': [{'username': 'Ghost'}, {'username': 'keep'}]})
        assert xras_cache.drop_pending_worklist_row('ghost') is True
        rows = xras_cache.load_pending_worklist()['rows']
        assert [r['username'] for r in rows] == ['keep']


# the coherence patch

class TestTheIndexInsert:
    """``insert`` adds a request SAM filed; without it an absent number stays absent."""

    def test_an_absent_request_is_appended_only_on_insert(self, published):
        fresh = request_index_entry(_payload('NCAR4100'))
        assert xras_cache.patch_requests_index('NCAR4100', fresh) is False
        assert xras_cache.patch_requests_index('NCAR4100', fresh, insert=True) is True
        rows = xras_cache.load_requests_index()['rows']
        assert [r['request_number'] for r in rows] == ['EXAM0001', 'NCAR4100']

    def test_a_present_request_is_replaced_not_duplicated(self, published):
        again = request_index_entry(_payload(status='Incomplete'))
        assert xras_cache.patch_requests_index('EXAM0001', again, insert=True) is True
        rows = xras_cache.load_requests_index()['rows']
        assert len(rows) == 1 and rows[0]['status'] == 'Incomplete'

    def test_none_never_appends(self, published):
        assert xras_cache.patch_requests_index('NCAR4100', None, insert=True) is False
        assert len(xras_cache.load_requests_index()['rows']) == 1

    def test_refresh_passes_insert_through(self, published):
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [_payload('NCAR4100')]
        assert service.refresh_index_entry('NCAR4100', reader=reader, insert=True) is True
        assert service.refresh_index_entry('NCAR4101', reader=reader) is False


class TestTheSnapshotPatch:

    def test_it_produces_the_same_entry_shape_as_the_sweep(
            self, factory, published, monkeypatch):
        """The two-consumers rule, asserted rather than trusted."""
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [_payload(
            action_status='Incomplete')]
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: reader))

        client = MagicMock()
        client.withdraw_action.return_value = _result()
        service.withdraw_action(factory, request_number='EXAM0001',
                                request_id=900001, action_id=7,
                                pi_username='pi-user', operator='benkirk',
                                comment='stale', client=client)

        patched = xras_cache.load_requests_index()['rows'][0]
        assert set(patched) == set(published), \
            'a patched entry must carry exactly the sweep\'s keys'
        assert patched['actions'][0]['action_status'] == 'Incomplete'
        assert patched['actions'][0]['can_resubmit'] is True

    def test_a_patched_row_is_stamped_so_the_card_can_say_so(
            self, factory, published, monkeypatch):
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [_payload()]
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: reader))

        client = MagicMock()
        client.withdraw_action.return_value = _result()
        service.withdraw_action(factory, request_number='EXAM0001',
                                request_id=900001, action_id=7,
                                pi_username='pi-user', operator='benkirk',
                                comment='x', client=client)

        assert published['refreshed_at'] is None
        assert xras_cache.load_requests_index()['rows'][0]['refreshed_at'] \
            is not None

    def test_a_row_patched_out_of_cohort_stays_visible(
            self, factory, published, monkeypatch):
        """The operator must see the effect, not a vanishing row."""
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [_payload(
            status='Incomplete', action_status='Incomplete')]
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: reader))

        client = MagicMock()
        client.withdraw_action.return_value = _result()
        service.withdraw_action(factory, request_number='EXAM0001',
                                request_id=900001, action_id=7,
                                pi_username='pi-user', operator='benkirk',
                                comment='x', client=client)

        rows = xras_cache.load_requests_index()['rows']
        assert len(rows) == 1
        assert rows[0]['status'] == 'Incomplete'

    def test_a_failed_refresh_is_not_a_failed_write(
            self, factory, published, monkeypatch):
        reader = MagicMock()
        reader.get_request_family_by_number.side_effect = XrasSourceUnavailable('down')
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: reader))

        client = MagicMock()
        client.withdraw_action.return_value = _result()
        outcome = service.withdraw_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            pi_username='pi-user', operator='benkirk', comment='x',
            client=client)

        assert outcome.succeeded is True
        assert outcome.patched is False
        # And the snapshot is left intact rather than half-written.
        assert xras_cache.load_requests_index()['rows'] == [published]

    def test_no_patch_is_attempted_when_the_write_did_not_verify(
            self, factory, published, monkeypatch):
        called = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: called.append(n) or True)
        client = MagicMock()
        client.withdraw_action.return_value = _result(verified=False)
        service.withdraw_action(factory, request_number='EXAM0001',
                                request_id=900001, action_id=7,
                                pi_username='pi-user', operator='benkirk',
                                comment='x', client=client)
        assert called == []


# roles

class TestRoleChanges:

    def test_an_add_learns_its_role_id_at_completion(self, factory, session,
                                                     monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.add_role.return_value = _result('add_role',
                                               extra={'role_id': 580030})
        outcome = service.change_role(
            factory, add=True, request_number='EXAM0001', request_id=900001,
            username='newbie', operator='benkirk', xa_user='pi-user',
            role='User', client=client)

        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.role_id, row.role_type) == (580030, 'User')

    def test_a_removal_records_the_role_id_it_was_given(self, factory, session,
                                                        monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.remove_role.return_value = _result('remove_role')
        outcome = service.change_role(
            factory, add=False, request_number='EXAM0001', request_id=900001,
            username='newbie', operator='benkirk', xa_user='pi-user',
            role_id=580030, client=client)

        assert session.get(XrasRemediationEvent, outcome.event_id).role_id == 580030

    def test_an_unknown_role_is_refused_before_anything_is_written(
            self, factory, session):
        client = MagicMock()
        with pytest.raises(ValueError):
            service.change_role(factory, add=True, request_number='EXAM0001',
                                request_id=900001, username='newbie',
                                operator='benkirk', xa_user='pi-user',
                                role='Reviewer', client=client)
        assert client.add_role.call_count == 0

    def test_a_role_change_can_defer_the_card_patch(self, factory, monkeypatch):
        patched = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: patched.append(n) or True)
        client = MagicMock()
        client.remove_role.return_value = _result('remove_role')
        outcome = service.change_role(
            factory, add=False, request_number='EXAM0001', request_id=900001,
            username='newbie', operator='benkirk', xa_user='pi-user',
            role_id=580030, client=client, refresh=lambda: False)
        assert patched == [] and outcome.patched is False

    def test_the_role_choices_carry_every_spelling(self):
        choices = service.role_choices()
        assert {c['id'] for c in choices} == {13, 14, 19}
        assert {c['display'] for c in choices} == {'Project Lead',
                                                   'Project Admin', 'User'}


class TestTheEditorOps:
    """The request editor's service verbs — same audit + patch discipline as
    the action ops, one per operation."""

    def test_an_amount_edit_is_recorded_and_patched_on_success(
            self, factory, session, monkeypatch):
        seen = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: seen.append(n) or True)
        client = MagicMock()
        client.update_resource_amount.return_value = _result(
            'update_resource_amount', verified=True,
            before=[{'resourceId': 530201, 'amount': '555'}],
            after=[{'resourceId': 530201, 'amount': '556'}])

        outcome = service.update_resource_amount(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            resource_id=530201, amount='556', pi_username='pi-user',
            operator='benkirk', client=client)

        assert outcome.status == 'verified'
        assert seen == ['EXAM0001']            # patched exactly once, by number
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert row.operation == 'update_resource_amount'
        assert (row.request_number, row.request_id, row.action_id) == \
            ('EXAM0001', 900001, 7)
        assert (row.xa_user, row.created_by) == ('pi-user', 'benkirk')

    def test_the_context_reaches_the_client(self, factory, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.update_resource_amount.return_value = _result(
            'update_resource_amount', verified=True, before=[], after=[])
        service.update_resource_amount(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            resource_id=530201, amount='20', pi_username='pi-user',
            operator='benkirk', context='admin', client=client)
        assert client.update_resource_amount.call_args.kwargs['context'] == 'admin'

    def test_a_rejected_amount_is_recorded_and_not_patched(
            self, factory, session, monkeypatch):
        called = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: called.append(n) or True)
        client = MagicMock()
        client.update_resource_amount.side_effect = XrasWriteRejected(
            'refused', status=400, errors=['Budget exceeds the limit'])

        outcome = service.update_resource_amount(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            resource_id=530201, amount='1', pi_username='pi-user',
            operator='benkirk', client=client)

        assert outcome.status == 'rejected'
        assert called == []                    # no patch on a rejection
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.status, row.http_status) == ('rejected', 400)

    def test_an_unverified_amount_is_not_reported_as_success(
            self, factory, session, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.update_resource_amount.return_value = _result(
            'update_resource_amount', verified=False, before=[], after=[])
        outcome = service.update_resource_amount(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            resource_id=530201, amount='1', pi_username='pi-user',
            operator='benkirk', client=client)
        assert outcome.status == 'unverified' and outcome.succeeded is False

    def test_remove_resource_records_its_operation(self, factory, session,
                                                   monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.remove_resource.return_value = _result(
            'remove_resource', verified=True,
            before=[{'resourceId': 530201}], after=[])
        outcome = service.remove_resource(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            resource_id=530201, pi_username='pi-user', operator='benkirk',
            client=client)
        assert session.get(XrasRemediationEvent,
                           outcome.event_id).operation == 'remove_resource'

    def test_set_and_remove_dates_record_their_operations(
            self, factory, session, monkeypatch):
        import datetime as dt
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.set_action_dates.return_value = _result(
            'set_action_dates', verified=True, before=[],
            after=[{'allocationDateId': 9}])
        client.remove_action_dates.return_value = _result(
            'remove_action_dates', verified=True,
            before=[{'allocationDateId': 9}], after=[])

        set_outcome = service.set_action_dates(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            begin_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 12, 31),
            pi_username='pi-user', operator='benkirk', client=client)
        rm_outcome = service.remove_action_dates(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            allocation_date_id=9, pi_username='pi-user', operator='benkirk',
            client=client)

        assert session.get(XrasRemediationEvent,
                           set_outcome.event_id).operation == 'set_action_dates'
        assert session.get(
            XrasRemediationEvent,
            rm_outcome.event_id).operation == 'remove_action_dates'

    def test_update_attributes_records_and_passes_wire_fields(
            self, factory, session, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.update_request_attributes.return_value = _result(
            'update_attributes', verified=True, before={'title': 'Old'},
            after={'title': 'New'})
        outcome = service.update_request_attributes(
            factory, request_number='EXAM0001', request_id=900001,
            fields={'title': 'New', 'shortTitle': '', 'abstract': 'A'},
            pi_username='pi-user', operator='benkirk', client=client)
        assert session.get(XrasRemediationEvent,
                           outcome.event_id).operation == 'update_attributes'
        # the wire-named dict reaches the client verbatim
        assert client.update_request_attributes.call_args.kwargs['title'] == 'New'
        assert client.update_request_attributes.call_args.kwargs['shortTitle'] == ''

    def test_update_action_records_and_passes_wire_fields(
            self, factory, session, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.update_action.return_value = _result(
            'update_action', verified=True, before={'userComments': None},
            after={'userComments': 'hi'})
        outcome = service.update_action(
            factory, request_number='EXAM0001', request_id=900001, action_id=7,
            fields={'userComments': 'hi'}, pi_username='pi-user',
            operator='benkirk', client=client)
        row = session.get(XrasRemediationEvent, outcome.event_id)
        assert (row.operation, row.action_id) == ('update_action', 7)
        assert client.update_action.call_args.kwargs['userComments'] == 'hi'

    def test_delete_request_records_and_patches_on_success(
            self, factory, session, monkeypatch):
        seen = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: seen.append(n) or True)
        client = MagicMock()
        client.delete_request.return_value = _result(
            'delete_request', verified=True,
            before={'requestNumber': 'EXAM0001'}, after=None)
        outcome = service.delete_request(
            factory, request_number='EXAM0001', request_id=900001,
            pi_username='pi-user', operator='benkirk', client=client)
        assert outcome.status == 'verified'
        assert seen == ['EXAM0001']   # the patch re-reads, finds nothing, drops it
        assert session.get(XrasRemediationEvent,
                           outcome.event_id).operation == 'delete_request'

    def test_renew_and_add_action_record_their_operations(
            self, factory, session, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = MagicMock()
        client.renew_request.return_value = _result(
            'renew_request', verified=True, before={}, after={})
        client.add_action.return_value = _result(
            'add_action', verified=True, before={}, after={})
        ro = service.renew_request(
            factory, request_number='EXAM0001', request_id=900001,
            pi_username='pi-user', operator='benkirk', client=client)
        ao = service.add_action(
            factory, request_number='EXAM0001', request_id=900001,
            action_type='Supplement', pi_username='pi-user', operator='benkirk',
            client=client)
        assert session.get(XrasRemediationEvent,
                           ro.event_id).operation == 'renew_request'
        assert session.get(XrasRemediationEvent,
                           ao.event_id).operation == 'add_action'
        assert client.add_action.call_args.args[1] == 'Supplement'


# the read side

class TestListing:

    def test_a_username_filter_matches_either_side_of_a_merge(self, session):
        make_xras_remediation_event(session, operation='merge_person',
                                    username='ghost-user-abcde',
                                    target_username='real-person')
        found = _rows(session, username='real-person')
        assert [e.username for e in found] == ['ghost-user-abcde']

    def test_newest_first(self, session):
        from datetime import timedelta
        now = datetime.now()
        make_xras_remediation_event(session, when=now - timedelta(days=2),
                                    request_number='OLD00001',
                                    operation='withdraw_action')
        make_xras_remediation_event(session, when=now,
                                    request_number='NEW00001',
                                    operation='withdraw_action')
        found = _rows(session, operation='withdraw_action')
        # Only this test's rows: other xdist workers commit withdraw_action rows
        # (test_xras_admin_client's EXAM0001) that can be newer than ours.
        mine = [e.request_number for e in found
                if e.request_number in ('OLD00001', 'NEW00001')]
        assert mine == ['NEW00001', 'OLD00001']

    def test_the_summary_counts_what_needs_a_human(self, session):
        events = [
            make_xras_remediation_event(session, status='verified'),
            make_xras_remediation_event(session, status='attempted'),
            make_xras_remediation_event(session, status='unverified'),
            make_xras_remediation_event(session, status='rejected'),
        ]
        summary = remediation_summary(events)
        assert summary['total'] == 4
        # `attempted` + `unverified` — a rejection needs nobody, it did nothing.
        assert summary['needs_attention'] == 2


class TestTheRefreshRerunsThePreflight:
    """A patched entry must carry a verdict, or the request it just fixed
    leaves the pending-work queue until the next sweep."""

    def test_a_refresh_with_a_factory_carries_verdicts(self, factory, published,
                                                       monkeypatch):
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [_payload()]
        monkeypatch.setattr(
            service, '_preflight_verdicts',
            lambda r, s, p: ({7: {'status': 'failed', 'messages': ['x']}},
                             {'failed': 1}))
        assert service.refresh_index_entry('EXAM0001', reader=reader,
                                            session_factory=factory) is True
        row = xras_cache.load_requests_index()['rows'][0]
        assert row['preflight_rollup'] == 'failed'
        assert row['refreshed_at']

    def test_the_merge_threads_its_factory_through(self, factory, published,
                                                   monkeypatch):
        seen = []
        monkeypatch.setattr(
            service, 'refresh_index_entry',
            lambda n, **kw: seen.append(kw.get('session_factory')) or True)
        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')
        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='pi-user', operator='benkirk',
                                  client=client)
        assert seen == [factory]


class TestDeletedLinesAreNotTheRequest:
    """A deleted line is no handoff: the refresh and the re-check read live lines only."""

    def test_a_refresh_drops_a_wholly_deleted_request(self, published):
        deleted = _payload()
        deleted['isDeleted'] = True
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [deleted]
        service.refresh_index_entry('EXAM0001', reader=reader)
        assert xras_cache.load_requests_index()['rows'] == []

    def test_a_recheck_reads_the_live_line_past_a_newer_deleted_one(
            self, published, session, monkeypatch):
        newer = _payload()
        newer.update(requestId=900002, isDeleted=True,
                     actions=[{'actionId': 9, 'actionType': 'Renewal',
                               'actionStatus': 'Approved'}])
        reader = MagicMock()
        reader.get_request_family_by_number.return_value = [newer, _payload()]
        checked = []
        monkeypatch.setattr(service, '_preflight_verdicts',
                            lambda r, s, p: checked.append(p['requestId']) or ({}, {}))
        result = service.recheck_readiness('EXAM0001', session=session, reader=reader)
        assert result['patched'] and checked == [900001]
        assert xras_cache.load_requests_index()['rows'][0]['request_id'] == 900001


class TestTheRenameMovesTheNote:
    """``_rekey_overrides`` moves a recommender note too, on its own committed session."""

    @pytest.fixture
    def numbers(self, engine):
        from sqlalchemy import delete
        from factories import next_seq
        from sam.integration.xras import XrasRequestNote
        pair = (next_seq('NCARN'), next_seq('UXYZN'))
        yield pair
        with engine.begin() as conn:
            conn.execute(delete(XrasRequestNote).where(XrasRequestNote.request_number.in_(pair)))

    def _note(self, factory, number, run_id):
        from factories import make_request_note
        with factory() as s:
            make_request_note(s, request_number=number, run_id=run_id)
            s.commit()

    def _run_ids(self, factory, number):
        from sam.integration.xras import lookup_request_notes
        with factory() as s:
            return [n.run_id for n in lookup_request_notes(s, number)]

    def test_the_note_follows_the_rename(self, factory, numbers):
        old, new = numbers
        self._note(factory, old, 'r1')
        service._rekey_overrides(factory, old, new)
        assert self._run_ids(factory, old) == [] and self._run_ids(factory, new) == ['r1']

    def test_a_note_on_the_new_number_wins_and_is_logged(self, factory, numbers, caplog):
        old, new = numbers
        self._note(factory, old, 'old')
        self._note(factory, new, 'new')
        service._rekey_overrides(factory, old, new)
        assert self._run_ids(factory, new) == ['new'] and self._run_ids(factory, old) == ['old']
        assert 'notes from arc-allocation-recommender left on' in caplog.text


class TestTheRename:
    """SAM's rename: pre-read by id, act as the PI, move the card row and the overrides."""

    @pytest.fixture
    def rekeyed(self, monkeypatch):
        seen = []
        monkeypatch.setattr(service, '_rekey_overrides',
                            lambda f, old, new: seen.append((old, new)))
        return seen

    def _client(self, number):
        client = MagicMock()
        client.reader.get_request_by_id.return_value = _payload(number=number)
        client.rename_request.return_value = _result(
            'rename_request', before={'requestNumber': 'NCAR0001'},
            after={'requestNumber': 'UXYZ0001'})
        return client

    def _rename(self, factory, client):
        return service.rename_request(factory, request_id=900001, old_number='NCAR0001',
                                      new_number='UXYZ0001', operator='task:xras_rename',
                                      client=client)

    def test_it_renames_as_the_pi_and_records_a_task_row(
            self, factory, session, rekeyed, monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = self._client('NCAR0001')
        outcome = self._rename(factory, client)
        assert outcome.status == 'verified'
        assert client.rename_request.call_args.kwargs == {
            'old_number': 'NCAR0001', 'new_number': 'UXYZ0001', 'xa_user': 'pi-user'}
        event = session.get(XrasRemediationEvent, outcome.event_id)
        assert (event.operation, event.created_by, event.request_number) == (
            'rename_request', 'task:xras_rename', 'NCAR0001')
        assert rekeyed == [('NCAR0001', 'UXYZ0001')]

    def test_an_already_renamed_line_costs_no_write(self, factory, rekeyed):
        client = self._client('UXYZ0001')
        outcome = self._rename(factory, client)
        assert outcome.status == 'already_renamed' and outcome.event_id is None
        assert outcome.status in service.RENAME_DONE
        client.rename_request.assert_not_called()
        assert rekeyed == [('NCAR0001', 'UXYZ0001')]

    def test_a_line_answering_a_third_number_is_left_alone(self, factory, rekeyed):
        client = self._client('ZZZZ9999')
        outcome = self._rename(factory, client)
        assert outcome.status == 'skipped' and 'ZZZZ9999' in outcome.error
        client.rename_request.assert_not_called()
        assert rekeyed == []

    def test_no_role_holder_means_no_write(self, factory, rekeyed):
        client = self._client('NCAR0001')
        client.reader.get_request_by_id.return_value['roles'] = []
        assert self._rename(factory, client).status == 'no_role_holder'
        client.rename_request.assert_not_called()

    def test_the_allocation_manager_stands_in_for_a_missing_pi(self, factory, rekeyed,
                                                                monkeypatch):
        monkeypatch.setattr(service, 'refresh_index_entry', lambda n, **kw: True)
        client = self._client('NCAR0001')
        client.reader.get_request_by_id.return_value['roles'] = [
            {'person': {'username': 'am-user'},
             'roles': [{'roleId': 3, 'role': 'Allocation Manager', 'roleTypeId': 14}]}]
        self._rename(factory, client)
        assert client.rename_request.call_args.kwargs['xa_user'] == 'am-user'

    def test_a_failed_pre_read_is_an_error_without_a_row(self, factory, rekeyed):
        client = self._client('NCAR0001')
        client.reader.get_request_by_id.side_effect = XrasSourceUnavailable('down')
        outcome = self._rename(factory, client)
        assert outcome.status == 'error' and outcome.event_id is None

    def test_an_unverified_rename_moves_nothing(self, factory, rekeyed, monkeypatch):
        refreshed = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: refreshed.append(n) or True)
        client = self._client('NCAR0001')
        client.rename_request.return_value = _result('rename_request', verified=False)
        assert self._rename(factory, client).status == 'unverified'
        assert rekeyed == [] and refreshed == []

    def test_the_card_row_moves_to_the_new_number(self, factory, rekeyed, monkeypatch):
        """Read by the new number, patched into the row keyed by the old, no longer pending."""
        entry = request_index_entry(_payload(number='NCAR0001'), pending_push=True)
        xras_cache.store_requests_index({'generated_at': datetime.now(), 'rows': [entry]})
        reader = MagicMock()
        reader.get_request_family_by_number.side_effect = (
            lambda n: [_payload(number='UXYZ0001')] if n == 'UXYZ0001' else [])
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: reader))
        outcome = self._rename(factory, self._client('NCAR0001'))
        assert outcome.patched is True
        rows = xras_cache.load_requests_index()['rows']
        assert [r['request_number'] for r in rows] == ['UXYZ0001']
        assert rows[0]['pending_push'] is False

    @pytest.mark.parametrize('rename, write, armed', [
        ('1', True, True), ('0', True, False), ('1', False, False), (None, True, False)])
    def test_the_route_needs_its_own_lever_and_the_write_lever(self, monkeypatch, rename,
                                                               write, armed):
        if rename is None:
            monkeypatch.delenv('XRAS_RENAME_ENABLED', raising=False)
        else:
            monkeypatch.setenv('XRAS_RENAME_ENABLED', rename)
        monkeypatch.setattr('sam.integration.xras_api.config.xras_write_configured',
                            lambda *a: write)
        assert service.rename_armed() is armed


# make PI

def _member(username, role, type_id, role_id):
    return {'username': username, 'role_type': role, 'role_type_id': type_id,
            'role_id': role_id}


_ADVISOR_AND_STUDENT = [_member('advisor', 'PI', 13, 1),
                        _member('student', 'Allocation Manager', 14, 3)]


class TestMakePi:
    """The order XRAS allows: SAM acts as the PI, and a PI cannot remove their only role."""

    def _client(self, fail_on=None):
        client = MagicMock()
        calls = []

        def add(request_id, role, username, *, request_number, xa_user):
            calls.append(('add', username, role.name, xa_user))
            if fail_on == ('add', username):
                raise XrasWriteRejected('nope', status=400)
            return _result('add_role', extra={'role_id': 99})

        def remove(request_id, role_id, *, request_number, xa_user):
            calls.append(('remove', role_id, xa_user))
            if fail_on == ('remove', role_id):
                raise XrasWriteRejected("can't delete your only role", status=400)
            return _result('remove_role')
        client.add_role.side_effect = add
        client.remove_role.side_effect = remove
        return client, calls

    def _run(self, factory, monkeypatch, roster, client, target_role_id=3):
        patched = []
        monkeypatch.setattr(service, 'refresh_index_entry',
                            lambda n, **kw: patched.append(n) or True)
        outcome = service.make_pi(
            factory, request_number='EXAM0001', request_id=900001, roster=roster,
            target_role_id=target_role_id, operator='benkirk', xa_user='advisor',
            client=client)
        return outcome, patched

    def test_the_swap_runs_in_the_order_xras_allows(self, factory, session, monkeypatch):
        client, calls = self._client()
        outcome, patched = self._run(factory, monkeypatch, _ADVISOR_AND_STUDENT, client)
        assert calls == [('add', 'student', 'PI', 'advisor'),
                         ('add', 'advisor', 'User', 'student'),
                         ('remove', 1, 'student'),
                         ('remove', 3, 'student')]
        assert outcome.completed and outcome.failure is None
        assert outcome.landed == ('added student as PI', 'kept advisor as User',
                                  'removed advisor as PI',
                                  'removed student as Allocation Manager')
        assert patched == ['EXAM0001'], 'the card is patched once, at the end'
        mine = factory.created[XrasRemediationEvent]
        ops = [r.operation for r in session.query(XrasRemediationEvent)
               .filter(XrasRemediationEvent.xras_remediation_event_id.in_(mine))
               .order_by(XrasRemediationEvent.xras_remediation_event_id)]
        assert ops == ['add_role', 'add_role', 'remove_role', 'remove_role']

    def test_an_old_pi_already_holding_user_is_not_added_again(self, factory, monkeypatch):
        roster = _ADVISOR_AND_STUDENT + [_member('advisor', 'User', 19, 2)]
        client, calls = self._client()
        outcome, _ = self._run(factory, monkeypatch, roster, client)
        assert [c[0] for c in calls] == ['add', 'remove', 'remove']
        assert outcome.completed

    def test_it_stops_at_the_first_failure_and_says_which(self, factory, monkeypatch):
        client, calls = self._client(fail_on=('remove', 1))
        outcome, patched = self._run(factory, monkeypatch, _ADVISOR_AND_STUDENT, client)
        assert len(calls) == 3, 'nothing after the failed step is sent'
        assert not outcome.completed
        assert outcome.landed == ('added student as PI', 'kept advisor as User')
        label, failed = outcome.failure
        assert label == 'removing advisor as PI' and failed.status == 'rejected'
        assert patched == ['EXAM0001'], 'what landed still reaches the card'

    def test_a_refused_first_step_changes_nothing_and_patches_nothing(
            self, factory, monkeypatch):
        client, calls = self._client(fail_on=('add', 'student'))
        outcome, patched = self._run(factory, monkeypatch, _ADVISOR_AND_STUDENT, client)
        assert len(calls) == 1 and outcome.landed == () and patched == []



class TestTheReadyMerge:
    """The Accounts card's "Ready to merge" set, merged by the reconcile task (XRAS_AUTO_MERGE)."""

    @pytest.fixture
    def ready(self, monkeypatch):
        targets = []
        monkeypatch.setattr(service, 'ready_to_merge', lambda session, **kw: list(targets))
        monkeypatch.setattr('sam.integration.xras.task_merge_attempts',
                            lambda session, names: set())
        return targets

    @pytest.fixture
    def merged(self, monkeypatch):
        calls = []

        def fake(factory, **kw):
            calls.append(kw)
            return MagicMock(succeeded=True)
        monkeypatch.setattr(service, 'merge_placeholder', fake)
        return calls

    @staticmethod
    def _target(source='kmerk-user-92wap', into='katarinam'):
        return {'username': source, 'target_username': into, 'target_active': True,
                'email': 'k@uni.example'}

    def test_a_ready_placeholder_merges_as_the_task(self, factory, ready, merged):
        ready.append(self._target())
        client = MagicMock()
        client.get_person.return_value = {'username': 'x'}
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['merged'] == [('kmerk-user-92wap', 'katarinam')]
        assert merged[0]['source_username'] == 'kmerk-user-92wap'
        assert merged[0]['operator'] == 'task:t' and 'resolves to katarinam' in merged[0]['comment']

    def test_a_target_xras_does_not_hold_waits(self, factory, ready, merged):
        ready.append(self._target())
        client = MagicMock()
        client.get_person.side_effect = lambda name: None if name == 'katarinam' else {'u': name}
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['waiting'] == 1 and merged == []

    def test_a_placeholder_already_gone_is_not_merged(self, factory, ready, merged):
        ready.append(self._target())
        client = MagicMock()
        client.get_person.return_value = None
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['gone'] == 1 and merged == []

    def test_gone_is_recorded_once_and_never_asked_about_again(self, factory, merged,
                                                               monkeypatch):
        """NCAR4434, 2026-11-05: XRAS had no such person, and the task asked every 15 minutes."""
        monkeypatch.setattr(service, 'ready_to_merge', lambda session, **kw: [self._target()])
        client = MagicMock()
        client.get_person.return_value = None
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['gone'] == 1 and merged == []
        with factory() as s:
            row, = s.query(XrasRemediationEvent).filter(
                XrasRemediationEvent.username == 'kmerk-user-92wap',
                XrasRemediationEvent.created_by == 'task:t').all()
            assert row.operation == 'merge_person' and row.status == 'rejected'
            assert row.target_username == 'katarinam'
            assert 'no such person' in row.outcome_reason
        again = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert again['refused'] == 1 and again['gone'] == 0
        assert client.get_person.call_count == 1

    def test_the_window_reaches_both_passes(self, factory, monkeypatch):
        seen = []
        monkeypatch.setattr(service, 'ready_to_merge',
                            lambda session, **kw: seen.append(kw) or [])
        since = datetime(2026, 8, 7)
        client = MagicMock()
        client.get_person.return_value = {'u': 1}
        service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client,
                                         since=since)
        service.close_stale_placeholders(factory, operator='task:t', cap=5, client=client,
                                         since=since)
        assert seen == [{'since': since}, {'since': since}]

    def test_a_placeholder_a_task_already_tried_is_never_retried(self, factory, ready, merged,
                                                               monkeypatch):
        ready.append(self._target())
        monkeypatch.setattr('sam.integration.xras.task_merge_attempts',
                            lambda session, names: {'kmerk-user-92wap'})
        client = MagicMock()
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['refused'] == 1 and merged == []
        client.get_person.assert_not_called()

    def test_the_cap(self, factory, ready, merged):
        ready.extend(self._target(f'p{i}-user-aaaaa', f'u{i}') for i in range(4))
        client = MagicMock()
        client.get_person.return_value = {'u': 1}
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=2, client=client)
        assert len(out['merged']) == 2 and len(merged) == 2

    def test_an_outage_stops_the_pass(self, factory, ready, merged):
        ready.extend([self._target(), self._target('other-user-bbbbb', 'u2')])
        client = MagicMock()
        client.get_person.side_effect = XrasSourceUnavailable('down')
        out = service.merge_ready_placeholders(factory, operator='task:t', cap=5, client=client)
        assert out['waiting'] == 1 and merged == []

    def test_the_ready_set_keeps_only_active_merge_targets(self, session, monkeypatch):
        from types import SimpleNamespace
        from sam.queries import xras_accounts, xras_identity_report
        monkeypatch.setattr(xras_accounts, 'load_pending_worklist_rows',
                            lambda: SimpleNamespace(checked=True, rows=[]))
        monkeypatch.setattr(xras_accounts, 'assemble_worklist',
                            lambda *a, **k: SimpleNamespace(rows=[]))
        monkeypatch.setattr(xras_identity_report, 'identity_merge_report', lambda rows: {'targets': [
            self._target(), {'username': 'x-user-ccccc', 'target_username': None,
                             'target_active': False}]})
        assert [t['username'] for t in service.ready_to_merge(session)] == ['kmerk-user-92wap']

    def test_the_ready_set_reads_the_index_and_the_window(self, session, monkeypatch):
        """The index's current rosters retire a stale push; the window bounds the pushes read."""
        from types import SimpleNamespace
        from sam.queries import xras_accounts
        seen = {}
        monkeypatch.setattr(xras_accounts, 'load_pending_worklist_rows',
                            lambda: SimpleNamespace(checked=True, rows=[]))
        monkeypatch.setattr(xras_accounts, 'assemble_worklist',
                            lambda *a, **k: seen.update(k) or SimpleNamespace(rows=[]))
        rows = [{'request_number': 'NCAR4434', 'roster': [{'username': 'katarinam'}]}]
        built = datetime(2026, 11, 5, 4)
        xras_cache.store_requests_index({'generated_at': built, 'rows': rows})
        since = datetime(2026, 8, 7)
        assert service.ready_to_merge(session, since=since) == []
        assert seen['since'] == since and seen['index'].rows == rows
        assert seen['index'].generated_at == built


class TestTheStaleClose:
    """A fulfilled placeholder request no pending push names, closed once XRAS drops it."""

    @pytest.fixture
    def rows(self, engine, monkeypatch):
        from sqlalchemy.orm import Session

        from factories import next_seq
        from sam.core.account_requests import AccountRequest
        from sam.core.users import User
        monkeypatch.setattr(service, 'ready_to_merge', lambda session, **kw: [])
        n = next_seq('8')
        made = {}
        with Session(engine) as s:
            user = s.query(User).filter(User.is_active).order_by(User.user_id).first()
            for tag in ('gone', 'kept'):
                row = AccountRequest.create(
                    s, email=f'zz.stale.{tag}.{n}@example.invalid', first_name='S',
                    last_name='Tale', purpose='submission', created_by='task:xras_sweep',
                    verified_by='task:xras_sweep', xras_username=f'st{tag}{n}-user-abcde')
                row.fulfill(user)
                made[tag] = (row.account_request_id, row.xras_username)
            s.commit()
        yield made
        with Session(engine) as s:
            s.query(AccountRequest).filter(AccountRequest.account_request_id.in_(
                [v[0] for v in made.values()])).delete(synchronize_session=False)
            s.commit()

    def test_gone_is_dismissed_and_kept_is_left(self, factory, engine, rows):
        from sqlalchemy.orm import Session

        from sam.core.account_requests import AccountRequest
        client = MagicMock()
        client.get_person.side_effect = lambda name: None if name == rows['gone'][1] else {'u': name}
        out = service.close_stale_placeholders(factory, operator='task:t', cap=500, client=client)
        assert out["closed"] >= 1 and out["kept"] >= 1
        with Session(engine) as s:
            gone, kept = s.get(AccountRequest, rows['gone'][0]), s.get(AccountRequest, rows['kept'][0])
            assert gone.state == 'dismissed' and 'no longer holds' in gone.closed_reason
            assert kept.state == 'submitted'

    def test_an_outage_closes_nothing(self, factory, engine, rows):
        from sqlalchemy.orm import Session

        from sam.core.account_requests import AccountRequest
        client = MagicMock()
        client.get_person.side_effect = XrasSourceUnavailable('down')
        out = service.close_stale_placeholders(factory, operator='task:t', cap=500, client=client)
        assert out == {'checked': 0, 'closed': 0, 'kept': 0}
        with Session(engine) as s:
            assert s.get(AccountRequest, rows['gone'][0]).state == 'submitted'
