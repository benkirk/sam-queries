"""The XRAS account-creation worklist.

The predicate under test is the one that decides an operator's work queue, so
three properties matter more than coverage breadth:

1. **Two classes, not one.** *Absent* and *inactive* block a handoff
   identically and need different remedies. A predicate that only checks
   existence silently drops the whole inactive half.
2. **``isAccountToBeCreated`` is never the predicate.** XRAS sets it when the
   role is created and never clears it, so it is true on people who have had
   working accounts for years.
3. **The feeds agree.** Feed A (inbound action log) and Feed B (outbound
   enumeration) reach the same classifier through the same
   :class:`RosterRecord` seam, and must classify identically.

⚠️ The in-tree fixtures are **scrubbed** — every username is rewritten to
``user_<hex>`` or ``placeholder<NN>-user-<NNNNN>``. So "no ``users`` row" is
trivially true for all of them, which proves the plumbing but not the
predicate. The predicate itself is validated against the unscrubbed corpus
outside the tree (see ``docs/xras/outgoing/`` § *The Tier-III test bed*); that
run is manual and recorded only as pass/fail, because nothing derived from
those payloads may enter a commit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from factories import make_user

from sam.queries.xras_accounts import (
    ActionRef,
    RosterRecord,
    classify_accounts,
    enrich_worklist,
    is_placeholder,
    records_from_action_log,
    records_from_report_requests,
    worklist_counts,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'xras' / 'actions'

#: An in-window placeholder identity. **Not** ``new_ncar4214_ok.json``: that
#: file's placeholder role ends 2026-07-28 against an action beginning
#: 2026-07-30, so the date window correctly excludes it — the role is over.
PLACEHOLDER_FIXTURE = 'new_ncar4227_failed.json'
PLACEHOLDER_USERNAME = 'placeholder38-user-00038'


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _record(*usernames, roles=None, flags=None, people=None, **ref_kwargs):
    """A RosterRecord built by hand, bypassing both feeds."""
    defaults = dict(action_log_id=1, request_number='NCAR0001',
                    action_type='New', status='received',
                    received_time=datetime(2026, 8, 1, 12, 0))
    defaults.update(ref_kwargs)
    return RosterRecord(
        ref=ActionRef(**defaults),
        usernames=tuple(usernames),
        roles_by_username=roles or {u: ('User',) for u in usernames},
        account_flag=flags or {},
        person_by_username=people or {})


# ── the placeholder shape ───────────────────────────────────────────────

class TestPlaceholderDetection:

    @pytest.mark.parametrize('username', [
        'placeholder38-user-00038', 'jsmith-user-a1b2c3', 'x-user-1',
    ])
    def test_arc_placeholders_match(self, username):
        assert is_placeholder(username) is True

    @pytest.mark.parametrize('username', [
        'benkirk', 'user_00000033', '', 'user-00034', 'auser',
    ])
    def test_real_usernames_do_not(self, username):
        assert is_placeholder(username) is False


# ── the classifier ──────────────────────────────────────────────────────

class TestClassification:
    """Current-state against ``users`` — never the action's status."""

    def test_an_unknown_username_is_absent(self, session):
        rows = classify_accounts(session, [_record('nobody-user-99999')])
        assert len(rows) == 1
        assert rows[0]['classification'] == 'absent'
        assert rows[0]['remedy'] == 'create'
        assert rows[0]['placeholder'] is True

    def test_an_inactive_user_is_inactive_not_absent(self, session):
        """The half an existence-only predicate drops.

        Measured on the unscrubbed corpus: 5 of 9 real worklist rows.
        """
        user = make_user(session, active=False)
        rows = classify_accounts(session, [_record(user.username)])
        assert len(rows) == 1
        assert rows[0]['classification'] == 'inactive'
        assert rows[0]['remedy'] == 'reactivate'

    def test_an_active_user_is_dropped(self, session):
        user = make_user(session, active=True)
        assert classify_accounts(session, [_record(user.username)]) == []

    def test_the_two_classes_are_reported_together(self, session):
        active = make_user(session, active=True)
        inactive = make_user(session, active=False)
        rows = classify_accounts(
            session, [_record(active.username, inactive.username, 'ghost-user-1')])
        assert {r['username'] for r in rows} == {inactive.username, 'ghost-user-1'}
        # Absent sorts first: it is the larger piece of work.
        assert rows[0]['classification'] == 'absent'

    def test_a_locked_user_is_not_active(self, session):
        """``User.is_active`` is ``active AND NOT locked`` — use the hybrid."""
        user = make_user(session, active=True)
        user.locked = True
        session.flush()
        rows = classify_accounts(session, [_record(user.username)])
        assert [r['classification'] for r in rows] == ['inactive']

    def test_classification_ignores_the_action_status(self, session):
        """Regime-proof across the capture-only -> live-dispatch flip."""
        seen = set()
        for status in ('received', 'processed', 'failed', 'manual'):
            rows = classify_accounts(
                session, [_record('ghost-user-1', status=status)])
            seen.add(rows[0]['classification'])
        assert seen == {'absent'}


class TestTheStaleFlagTrap:
    """``isAccountToBeCreated`` is a hint column and nothing more."""

    def test_a_flagged_but_active_user_is_not_on_the_worklist(self, session):
        """The § 5 trap. On the unscrubbed corpus, 5 of 5 flagged usernames
        were existing *active* accounts — the flag was 100% stale."""
        user = make_user(session, active=True)
        rows = classify_accounts(
            session, [_record(user.username, flags={user.username: True})])
        assert rows == []

    def test_the_flag_still_rides_along_as_a_hint(self, session):
        rows = classify_accounts(
            session, [_record('ghost-user-1', flags={'ghost-user-1': True})])
        assert rows[0]['is_account_to_be_created'] is True

    def test_an_unflagged_absent_user_is_still_reported(self, session):
        """The inverse: the flag being false must not suppress a real case."""
        rows = classify_accounts(session, [_record('ghost-user-1')])
        assert rows[0]['is_account_to_be_created'] is False
        assert rows[0]['classification'] == 'absent'


class TestGrouping:
    """One row per username, across every action naming them."""

    def test_actions_are_grouped_and_newest_first(self, session):
        rows = classify_accounts(session, [
            _record('ghost-user-1', action_log_id=1,
                    received_time=datetime(2026, 8, 1)),
            _record('ghost-user-1', action_log_id=2,
                    received_time=datetime(2026, 8, 5)),
        ])
        assert len(rows) == 1
        assert [a['action_log_id'] for a in rows[0]['actions']] == [2, 1]
        assert rows[0]['first_seen'] == datetime(2026, 8, 1)
        assert rows[0]['last_seen'] == datetime(2026, 8, 5)
        # Deliberately the future notes-table FK target.
        assert rows[0]['latest_action_log_id'] == 2

    def test_roles_are_unioned_strongest_first(self, session):
        rows = classify_accounts(session, [
            _record('ghost-user-1', roles={'ghost-user-1': ('User',)}),
            _record('ghost-user-1', roles={'ghost-user-1': ('PI',)}),
            _record('ghost-user-1', roles={'ghost-user-1': ('Allocation Manager',)}),
        ])
        assert rows[0]['roles'] == ('PI', 'Allocation Manager', 'User')

    def test_counts_cover_every_facet(self, session):
        inactive = make_user(session, active=False)
        rows = classify_accounts(
            session, [_record('ghost-user-1', inactive.username)])
        counts = worklist_counts(rows)
        assert counts['total'] == 2
        assert counts['absent'] == 1 and counts['inactive'] == 1
        assert counts['placeholder'] == 1


# ── Feed A ──────────────────────────────────────────────────────────────

class TestFeedA:
    """Rosters out of ``xras_action_log.raw_payload``."""

    def _log_row(self, session, payload_name, **kwargs):
        from sam.integration.xras import XrasActionLog
        row = XrasActionLog(
            received_time=kwargs.pop('received_time', datetime(2026, 8, 1)),
            remote_actor='XRAS',
            raw_payload=json.dumps(_payload(payload_name)),
            status=kwargs.pop('status', 'received'),
            **kwargs)
        session.add(row)
        session.flush()
        return row

    def test_it_extracts_the_roster_and_classifies_it(self, session):
        self._log_row(session, PLACEHOLDER_FIXTURE, action_type='New',
                      request_number='NCAR4227')
        records = records_from_action_log(session, validate=False)
        assert records, 'no roster came out of the action log'
        rows = classify_accounts(session, records)
        by_name = {r['username']: r for r in rows}
        assert PLACEHOLDER_USERNAME in by_name
        entry = by_name[PLACEHOLDER_USERNAME]
        assert entry['classification'] == 'absent'
        assert entry['placeholder'] is True
        assert entry['actions'][0]['request_number'] == 'NCAR4227'
        assert entry['actions'][0]['source'] == 'action_log'

    def test_statuses_bound_which_rows_are_read(self, session):
        self._log_row(session, PLACEHOLDER_FIXTURE, status='processed')
        assert records_from_action_log(
            session, statuses=('received',), validate=False) == []

    def test_an_unparseable_payload_is_skipped_not_fatal(self, session):
        from sam.integration.xras import XrasActionLog
        session.add(XrasActionLog(received_time=datetime(2026, 8, 1),
                                  remote_actor='XRAS', raw_payload='{not json',
                                  status='failed'))
        session.flush()
        # It is already visible on the action-log card as its own failure.
        assert records_from_action_log(session, validate=False) == []

    def test_the_window_bounds_the_read(self, session):
        self._log_row(session, PLACEHOLDER_FIXTURE,
                      received_time=datetime(2026, 8, 1))
        assert records_from_action_log(
            session, since=datetime(2026, 9, 1), validate=False) == []
        assert records_from_action_log(
            session, since=datetime(2026, 7, 1), validate=False)

    def test_a_role_outside_the_date_window_is_excluded(self, session):
        """``new_ncar4214_ok.json``'s placeholder role ended before the action
        began — the handoff does not need that account, so it is not work."""
        self._log_row(session, 'new_ncar4214_ok.json')
        records = records_from_action_log(session, validate=False)
        assert 'placeholder34-user-00034' not in records[0].usernames

    def test_the_validate_verdict_is_provenance_not_the_predicate(self, session):
        """It also catches mnemonic and resource-key failures, which are not
        account problems — so it annotates, it does not classify."""
        self._log_row(session, PLACEHOLDER_FIXTURE)
        records = records_from_action_log(session, validate=True)
        ref = records[0].ref
        assert ref.would_succeed in (True, False, None)
        assert isinstance(ref.reject_messages, tuple)
        rows = classify_accounts(session, records)
        # The classification stands on the users table alone.
        assert all(r['classification'] in ('absent', 'inactive') for r in rows)


# ── Feed B ──────────────────────────────────────────────────────────────

REPORT_REQUEST = {
    'requestId': 1446994,
    'requestNumber': 'NCAR9001',
    'requestStatus': 'Approved',
    'requestType': 'New',
    'roles': [
        {'person': {'username': 'ghost-user-77777',
                    'firstName': 'Ada', 'lastName': 'Invented',
                    'email': 'ada@example.invalid',
                    'organization': 'Example University',
                    'academicStatus': 'Graduate Student',
                    'residenceCountry': 'United States',
                    'isReconciled': False, 'orcid': None},
         'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13,
                    'beginDate': '2026-01-01', 'endDate': None,
                    'isAccountToBeCreated': True}]},
    ],
}


class TestFeedB:
    """Rosters out of ``GET /v1/reports/requests``. Shape verified live."""

    def test_it_reads_the_nested_outgoing_shape(self, session):
        records = records_from_report_requests([REPORT_REQUEST])
        assert records[0].usernames == ('ghost-user-77777',)
        # The outgoing wire nests person under the entry and names the role
        # `role`, not `roleType` — a different shape from the inbound payload.
        assert records[0].roles_by_username['ghost-user-77777'] == ('PI',)
        assert records[0].ref.action_log_id is None
        assert records[0].ref.source == 'reports'

    def test_the_person_object_arrives_inline(self, session):
        records = records_from_report_requests([REPORT_REQUEST])
        rows = classify_accounts(session, records)
        assert rows[0]['is_reconciled'] is False
        assert rows[0]['person']['residenceCountry'] == 'United States'

    def test_both_feeds_reach_the_same_classifier_identically(self, session):
        """The feed-agnostic proof."""
        feed_b = classify_accounts(session, records_from_report_requests(
            [REPORT_REQUEST]))
        feed_a = classify_accounts(session, [_record(
            'ghost-user-77777', roles={'ghost-user-77777': ('PI',)})])
        assert feed_b[0]['classification'] == feed_a[0]['classification']
        assert feed_b[0]['placeholder'] == feed_a[0]['placeholder']
        assert feed_b[0]['roles'] == feed_a[0]['roles']

    def test_a_request_with_no_resolvable_roster_is_skipped(self):
        assert records_from_report_requests([{'requestNumber': 'X'}]) == []
        assert records_from_report_requests([None, 'nonsense']) == []

    def test_rows_from_both_feeds_merge_onto_one_username(self, session):
        rows = classify_accounts(session, [
            *records_from_report_requests([REPORT_REQUEST]),
            _record('ghost-user-77777'),
        ])
        assert len(rows) == 1
        assert sorted(rows[0]['sources']) == ['action_log', 'reports']


# ── enrichment ──────────────────────────────────────────────────────────

class TestEnrichment:
    """Injected, so the query layer stays offline-capable."""

    def test_it_attaches_person_detail_and_the_closure_signal(self, session):
        rows = classify_accounts(session, [_record('ghost-user-1')])
        report = enrich_worklist(rows, person_lookup=lambda u: {
            'firstName': 'Ada', 'lastName': 'Invented',
            'email': 'ada@example.invalid', 'residenceCountry': 'Canada',
            'isReconciled': True})
        assert rows[0]['person']['residenceCountry'] == 'Canada'
        assert rows[0]['is_reconciled'] is True
        assert report['found'] == 1 and report['closed'] == 1

    def test_an_outage_degrades_rather_than_raising(self, session):
        """The card must render counts and usernames, not a 500."""
        from sam.integration.xras_api.base import XrasSourceUnavailable

        def boom(_username):
            raise XrasSourceUnavailable('down')

        rows = classify_accounts(session, [_record('ghost-user-1')])
        report = enrich_worklist(rows, person_lookup=boom)
        assert report['unavailable'] is True
        assert rows[0]['person'] is None
        assert rows[0]['classification'] == 'absent'

    def test_an_unconfigured_deployment_degrades_the_same_way(self, session):
        """``XrasApiNotConfigured`` is a subclass, so one branch covers both."""
        from sam.integration.xras_api.base import XrasApiNotConfigured

        def not_configured(_username):
            raise XrasApiNotConfigured('no key')

        rows = classify_accounts(session, [_record('ghost-user-1')])
        assert enrich_worklist(rows, person_lookup=not_configured)['unavailable']

    def test_the_lookup_budget_is_bounded(self, session):
        rows = classify_accounts(session, [
            _record(*[f'ghost-user-{i}' for i in range(10)])])
        report = enrich_worklist(rows, person_lookup=lambda u: None,
                                 max_lookups=3)
        assert report['looked_up'] == 3
        assert report['budget_exhausted'] is True

    def test_feed_b_rows_need_no_lookup(self, session):
        """The enumeration carries the person inline — no round trip."""
        calls = []
        rows = classify_accounts(session, records_from_report_requests(
            [REPORT_REQUEST]))
        report = enrich_worklist(rows,
                                 person_lookup=lambda u: calls.append(u))
        assert calls == []
        assert report['looked_up'] == 0

    def test_only_the_declared_person_fields_are_kept(self, session):
        rows = classify_accounts(session, [_record('ghost-user-1')])
        enrich_worklist(rows, person_lookup=lambda u: {
            'firstName': 'Ada', 'isReconciled': False,
            'internalXrasId': 'should not survive'})
        assert 'internalXrasId' not in rows[0]['person']


class TestImportGraph:
    """The task imports this module; ``test_task_ledger`` walks what that drags."""

    def test_it_is_not_exported_from_the_queries_package(self):
        """``sam/queries/__init__.py`` imports its submodules eagerly."""
        import sam.queries
        assert not hasattr(sam.queries, 'get_account_worklist')

    def test_it_does_not_import_the_api_client_at_module_scope(self):
        source = (Path(__file__).resolve().parents[2] / 'src' / 'sam' /
                  'queries' / 'xras_accounts.py').read_text()
        head = source.split('def ', 1)[0]
        assert 'xras_api' not in head, \
            'the API client must be imported lazily, inside enrich_worklist'
