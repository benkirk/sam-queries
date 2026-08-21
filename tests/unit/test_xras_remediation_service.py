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
from factories import make_xras_remediation_event  # noqa: F401  (fixture parity)

from sam.integration.xras import XrasRemediationEvent
from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.admin_client import XrasWriteResult
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

pytestmark = pytest.mark.unit


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
    return XrasWriteResult(operation=operation, method='DELETE', path='/p',
                           http_status=200, verified=verified,
                           verify_detail='detail', **kw)


@pytest.fixture
def factory(engine):
    """A real session factory — the service opens and commits its own."""
    from sqlalchemy.orm import Session
    return lambda: Session(engine)


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


# ── the audit row outlives the write ────────────────────────────────────

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


# ── merge ───────────────────────────────────────────────────────────────

class TestMerge:

    def test_both_cache_entries_are_invalidated(self, factory, monkeypatch):
        """Source because it is gone; target because its roles changed."""
        seen = []
        monkeypatch.setattr(xras_cache, 'invalidate_person', seen.append)
        monkeypatch.setattr(service, '_patch_requests_naming', lambda _u: True)

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
        """⚠️ The reason `before_state` exists at all.

        Merge does not copy person detail, so `residenceCountry` — which the
        inbound wire never carries either — exists nowhere SAM can reach once
        the source is deleted. This column is the only copy.

        Regression: the service captured it in the client result and never
        wrote it, leaving the column permanently NULL. Caught by a local DDL
        smoke on 2026-08-21, not by any test — hence this one.
        """
        monkeypatch.setattr(service, '_patch_requests_naming', lambda _u: True)
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
        monkeypatch.setattr(service, '_refresh_index_entry',
                            lambda n, **kw: refreshed.append(n) or True)
        monkeypatch.setattr(xras_cache, 'invalidate_person', lambda _u: None)

        client = MagicMock()
        client.merge_person.return_value = _result('merge_person')
        service.merge_placeholder(factory, source_username='ghost-user-abcde',
                                  target_username='real', operator='benkirk',
                                  client=client)
        assert refreshed == ['EXAM0001']


# ── the coherence patch ─────────────────────────────────────────────────

class TestTheSnapshotPatch:

    def test_it_produces_the_same_entry_shape_as_the_sweep(
            self, factory, published, monkeypatch):
        """The two-consumers rule, asserted rather than trusted."""
        reader = MagicMock()
        reader.get_request_by_number.return_value = _payload(
            action_status='Incomplete')
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
        reader.get_request_by_number.return_value = _payload()
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
        reader.get_request_by_number.return_value = _payload(
            status='Incomplete', action_status='Incomplete')
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
        reader.get_request_by_number.side_effect = XrasSourceUnavailable('down')
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
        monkeypatch.setattr(service, '_refresh_index_entry',
                            lambda n, **kw: called.append(n) or True)
        client = MagicMock()
        client.withdraw_action.return_value = _result(verified=False)
        service.withdraw_action(factory, request_number='EXAM0001',
                                request_id=900001, action_id=7,
                                pi_username='pi-user', operator='benkirk',
                                comment='x', client=client)
        assert called == []


# ── roles ───────────────────────────────────────────────────────────────

class TestRoleChanges:

    def test_an_add_learns_its_role_id_at_completion(self, factory, session,
                                                     monkeypatch):
        monkeypatch.setattr(service, '_refresh_index_entry', lambda n, **kw: True)
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
        monkeypatch.setattr(service, '_refresh_index_entry', lambda n, **kw: True)
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

    def test_the_role_choices_carry_every_spelling(self):
        choices = service.role_choices()
        assert {c['id'] for c in choices} == {13, 14, 19}
        assert {c['display'] for c in choices} == {'Project Lead',
                                                   'Project Admin', 'User'}


# ── the read side ───────────────────────────────────────────────────────

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
        assert found[0].request_number == 'NEW00001'

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

    def test_it_is_not_exported_from_the_queries_package(self):
        """Exporting it would drag the ORM into every `from sam.queries import`."""
        import sam.queries as queries
        assert not hasattr(queries, 'list_remediation_events')
