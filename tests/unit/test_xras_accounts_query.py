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

WARNING: The in-tree fixtures are **scrubbed** — every username is rewritten to
``user_<hex>`` or ``placeholder<NN>-user-<NNNNN>``. So "no ``users`` row" is
trivially true for all of them, which proves the plumbing but not the
predicate. The predicate itself is validated against the unscrubbed corpus
outside the tree (see ``docs/xras/outgoing/`` § *The Tier-III test bed*); that
run is manual and recorded only as pass/fail, because nothing derived from
those payloads may enter a commit.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from factories import make_email_address, make_user, make_xras_remediation_event

from sam.queries.xras_accounts import (
    PERSON_FIELDS,
    ActionRef,
    PendingFeed,
    RosterRecord,
    classify_accounts,
    enrich_worklist,
    sam_merge_targets,
    stamp_merge_targets,
    worklist_sort_key,
    is_placeholder,
    load_pending_worklist_rows,
    merge_worklists,
    records_from_action_log,
    records_from_report_requests,
    stamp_waiting_days,
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


@pytest.fixture
def no_committed_placeholder(serial_file_lock):
    """Hold off `test_xras_accounts_card.py`'s committed fixture rows.

    WARNING: that file COMMITs a real `users` row for PLACEHOLDER_USERNAME with
    `active=False`, under this same lock name. Any assertion here that pins a
    CLASSIFICATION for a username the JSON fixtures carry is a race against it:
    while the row exists the classifier correctly answers `inactive`, and the
    expected `absent` never appears. Asserting mere presence is safe and needs
    no lock. Reproduce by inserting that row by hand and running these two.
    """
    with serial_file_lock('xras_accounts_committed_fixtures'):
        yield


def _pending_record(*usernames, submit_date='2026-07-14', **kwargs):
    """A Feed-B RosterRecord: no arrival of its own, only a ``submitDate``.

    The inverse of :func:`_record`, and the pair is the point — the two feeds
    date a row from different fields, so anything deriving an age has to handle
    both or it silently reports one feed as undatable.
    """
    return _record(*usernames, action_log_id=None, received_time=None,
                   submit_date=submit_date, source='reports', **kwargs)


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


# the placeholder shape

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


# the classifier

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
        assert counts['merge_ready'] == 0


def _ghost(email, username='ghost-user-1', **over):
    person = {'email': email, 'firstName': 'G', 'lastName': 'Host'}
    return _record(username, people={username: person}, **over)


class TestMergeTargets:
    """The third remedy: SAM already holds the placeholder's email."""

    def test_an_active_holder_makes_the_row_ready_to_merge(self, session):
        holder = make_user(session)
        mail = make_email_address(session, holder)
        rows = classify_accounts(session, [_ghost(mail.email_address)])
        assert rows[0]['remedy'] == 'create', 'the classifier never stamps'
        stamp_merge_targets(session, rows)
        assert rows[0]['remedy'] == 'merge'
        assert rows[0]['merge_target'] == {'username': holder.username, 'active': True}
        assert worklist_counts(rows)['merge_ready'] == 1

    def test_an_inactive_holder_means_reactivate_then_merge(self, session):
        holder = make_user(session, active=False)
        mail = make_email_address(session, holder)
        rows = classify_accounts(session, [_ghost(mail.email_address)])
        stamp_merge_targets(session, rows)
        assert rows[0]['remedy'] == 'reactivate'
        assert rows[0]['merge_target'] == {'username': holder.username, 'active': False}

    def test_the_email_collation_is_binary_so_case_is_folded(self, session):
        """email_address.email_address is utf8mb3_bin, unlike users.username."""
        holder = make_user(session)
        make_email_address(session, holder, email='Ghost.Host@Example.INVALID')
        targets = sam_merge_targets(session, ['ghost.host@example.invalid'])
        assert targets['ghost.host@example.invalid']['username'] == holder.username

    def test_a_retired_address_does_not_vouch(self, session):
        holder = make_user(session)
        mail = make_email_address(session, holder, active=False)
        assert sam_merge_targets(session, [mail.email_address]) == {}

    def test_two_active_holders_are_ambiguous_and_yield_no_target(self, session):
        email = f'shared-{make_user(session).username}@example.invalid'
        make_email_address(session, make_user(session), email=email)
        make_email_address(session, make_user(session), email=email)
        rows = classify_accounts(session, [_ghost(email)])
        counts = stamp_merge_targets(session, rows)
        assert counts == {'ambiguous': 1}
        assert rows[0]['merge_target'] is None and rows[0]['remedy'] == 'create'

    def test_a_row_without_a_person_is_left_alone(self, session):
        rows = classify_accounts(session, [_record('ghost-user-2')])
        stamp_merge_targets(session, rows)
        assert rows[0]['merge_target'] is None and rows[0]['remedy'] == 'create'

    def test_a_snapshot_row_from_an_older_image_is_backfilled(self, session):
        """A cached row carries no key and `remedy: create`; the stamp decides."""
        holder = make_user(session)
        mail = make_email_address(session, holder)
        stale = {'username': 'ghost-user-3', 'classification': 'absent',
                 'remedy': 'create', 'placeholder': True, 'sources': ['reports'],
                 'person': {'email': mail.email_address}, 'actions': []}
        rows = [stale]
        stamp_merge_targets(session, rows)
        assert stale['remedy'] == 'merge'
        assert stale['merge_target']['username'] == holder.username

    def test_only_placeholders_are_stamped(self, session):
        holder = make_user(session)
        mail = make_email_address(session, holder)
        rows = classify_accounts(
            session, [_record('realname', people={'realname': {'email': mail.email_address}})])
        stamp_merge_targets(session, rows)
        assert rows[0]['merge_target'] is None and rows[0]['remedy'] == 'create'

    def test_ready_rows_sort_first_behind_received_pushes(self):
        rows = [
            {'username': 'b', 'remedy': 'create', 'sources': ['reports']},
            {'username': 'a', 'remedy': 'reactivate', 'sources': ['reports']},
            {'username': 'c', 'remedy': 'merge', 'sources': ['reports']},
            {'username': 'z', 'remedy': 'create', 'sources': ['action_log']},
        ]
        assert [r['username'] for r in sorted(rows, key=worklist_sort_key)] == \
            ['z', 'c', 'b', 'a']


# Feed A

class TestFeedA:
    """Rosters out of ``xras_action_log.raw_payload``."""

    #: WARNING: Assertions here must be scoped to the rows the test itself created.
    #: `tests/unit/test_xras_accounts_card.py` COMMITS an `xras_action_log` row
    #: AND a `users` row (its route reads through Flask-SQLAlchemy's own
    #: connection and only sees committed rows), and xdist workers share one
    #: database — so a bare `records_from_action_log(...) == []` is a race
    #: against that fixture, not an assertion about this test's data. A test
    #: pinning a classification for a fixture username needs
    #: `no_committed_placeholder`.

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

    def test_it_extracts_the_roster_and_classifies_it(
            self, session, no_committed_placeholder):
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

    def test_the_inline_person_makes_feed_a_need_no_lookup(
            self, session, no_committed_placeholder):
        """The POST body carried ``roles[].person`` all along; the worklist
        reads it instead of paying a ``GET /v1/people`` per row. A lookup
        that would raise proves none is attempted."""
        self._log_row(session, PLACEHOLDER_FIXTURE, action_type='New',
                      request_number='NCAR4227')
        rows = classify_accounts(
            session, records_from_action_log(session, validate=False))
        target = next(r for r in rows if r['username'] == PLACEHOLDER_USERNAME)
        assert target['person'], 'person did not arrive from the payload'
        assert set(target['person']) <= set(PERSON_FIELDS)

        def boom(_username):
            raise AssertionError('lookup attempted despite the inline person')

        report = enrich_worklist(rows, person_lookup=boom)
        assert report['looked_up'] == 0

    def test_a_later_real_post_supersedes_an_earlier_one(
            self, session, no_committed_placeholder):
        """A failed post stays failed forever; the re-post is the live roster."""
        old = self._log_row(session, PLACEHOLDER_FIXTURE, status='failed',
                            action_id=990388011, request_number='NCAR4227')
        new = self._log_row(session, PLACEHOLDER_FIXTURE, status='failed',
                            action_id=990388011, request_number='NCAR4227',
                            received_time=datetime(2026, 8, 2))
        ids = {r.ref.action_log_id
               for r in records_from_action_log(session, validate=False)}
        assert new.xras_action_log_id in ids
        assert old.xras_action_log_id not in ids

    def test_a_recheck_row_supersedes_nothing(self, session,
                                             no_committed_placeholder):
        post = self._log_row(session, PLACEHOLDER_FIXTURE, status='failed',
                             action_id=990388012, request_number='NCAR4227')
        self._log_row(session, PLACEHOLDER_FIXTURE, status='rechecked',
                      action_id=990388012, request_number='NCAR4227',
                      source_action_id=post.xras_action_log_id,
                      received_time=datetime(2026, 8, 2))
        ids = {r.ref.action_log_id
               for r in records_from_action_log(session, validate=False)}
        assert post.xras_action_log_id in ids

    def test_a_merged_away_identity_is_history_not_work(
            self, session, no_committed_placeholder):
        self._log_row(session, PLACEHOLDER_FIXTURE, action_type='New',
                      request_number='NCAR4227')
        make_xras_remediation_event(session, status='attempted',
                                    username=PLACEHOLDER_USERNAME,
                                    target_username='real')
        names = {u for r in records_from_action_log(session, validate=False)
                 for u in r.usernames}
        assert PLACEHOLDER_USERNAME in names, 'an attempt is not a merge'

        make_xras_remediation_event(session, status='verified',
                                    username=PLACEHOLDER_USERNAME.upper(),
                                    target_username='real')
        names = {u for r in records_from_action_log(session, validate=False)
                 for u in r.usernames}
        assert PLACEHOLDER_USERNAME not in names

    def test_statuses_bound_which_rows_are_read(self, session):
        row = self._log_row(session, PLACEHOLDER_FIXTURE, status='processed')
        ids = {r.ref.action_log_id for r in records_from_action_log(
            session, statuses=('received',), validate=False)}
        assert row.xras_action_log_id not in ids

    def test_an_unparseable_payload_is_skipped_not_fatal(self, session):
        from sam.integration.xras import XrasActionLog
        row = XrasActionLog(received_time=datetime(2026, 8, 1),
                            remote_actor='XRAS', raw_payload='{not json',
                            status='failed')
        session.add(row)
        session.flush()
        # It is already visible on the action-log card as its own failure.
        ids = {r.ref.action_log_id
               for r in records_from_action_log(session, validate=False)}
        assert row.xras_action_log_id not in ids

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

    def test_a_parked_action_is_not_reported_as_success(self, session):
        """Phase 0's trap: a Date Adjustment has no service, so dispatch parks
        it ``manual``. That must read as not-success, with the parking reason
        carried — never as ``would_succeed``."""
        self._log_row(session, 'date_adjustment_uwas0141_manual.json',
                      request_number='UWAS0141')
        records = records_from_action_log(session, validate=True)
        # Not records[0]: a sibling test may COMMIT its own action-log row into
        # the shared xdist DB, so scope to the row this test created.
        ref = next(r.ref for r in records if r.ref.request_number == 'UWAS0141')
        assert ref.preflight_status == 'manual'
        assert ref.would_succeed is False
        assert ref.reject_messages and 'service' in ref.reject_messages[0]

    def test_the_verdict_registers_the_handlers(self, session):
        """Handlers register only by import side effect; the CLI/sweep path
        imports none, so ``_validate`` must pull them in itself — otherwise
        every dispatch parks ``manual`` for the wrong reason."""
        import subprocess
        import sys
        # A fresh interpreter: dispatch starts with no handlers; importing what
        # _validate imports must populate the registry.
        code = (
            'import sam.xras.dispatch as d\n'
            'assert d.registered_services() == frozenset()\n'
            'import sam.xras.handlers  # what _validate imports\n'
            'assert d.registered_services(), "handlers did not register"\n'
        )
        proc = subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


# Feed B

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

    def test_action_type_reads_the_action_not_request_type(self, session):
        """``requestType`` is ``New``/``Renewal`` on every row and selects no
        handler; the dispatching type lives on the action."""
        payload = dict(REPORT_REQUEST, requestType='Renewal',
                       actions=[{'actionId': 1, 'actionType': 'Extension'}])
        records = records_from_report_requests([payload])
        assert records[0].ref.action_type == 'Extension'
        # With no actions, it falls back rather than inventing one.
        assert records_from_report_requests(
            [REPORT_REQUEST])[0].ref.action_type == 'New'

    def test_an_ended_role_window_excludes_the_person(self, session):
        """A person whose every role is dated out of range needs no account —
        the window rule the inbound roster already applies (the
        placeholder34 case in docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md)."""
        entry = dict(REPORT_REQUEST['roles'][0])
        entry['roles'] = [dict(entry['roles'][0], endDate='2026-07-28')]
        payload = dict(REPORT_REQUEST, roles=[entry])
        assert records_from_report_requests([payload]) == []

    def test_a_surviving_role_keeps_the_person_and_drops_the_ended_one(
            self, session):
        entry = dict(REPORT_REQUEST['roles'][0])
        entry['roles'] = [
            dict(entry['roles'][0], role='User', roleTypeId=19,
                 endDate='2026-07-28'),
            dict(entry['roles'][0], roleId=2),
        ]
        records = records_from_report_requests([dict(REPORT_REQUEST,
                                                     roles=[entry])])
        assert records[0].roles_by_username['ghost-user-77777'] == ('PI',)

    def test_a_future_dated_role_is_not_yet_in_window(self, session):
        entry = dict(REPORT_REQUEST['roles'][0])
        entry['roles'] = [dict(entry['roles'][0], beginDate='2126-01-01')]
        assert records_from_report_requests(
            [dict(REPORT_REQUEST, roles=[entry])]) == []

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


# enrichment

class TestEnrichment:
    """Injected, so the query layer stays offline-capable."""

    def test_it_attaches_person_detail_and_identity_state(self, session):
        rows = classify_accounts(session, [_record('ghost-user-1')])
        report = enrich_worklist(rows, person_lookup=lambda u: {
            'firstName': 'Ada', 'lastName': 'Invented',
            'email': 'ada@example.invalid', 'residenceCountry': 'Canada',
            'isReconciled': True})
        assert rows[0]['person']['residenceCountry'] == 'Canada'
        assert rows[0]['is_reconciled'] is True
        assert report['found'] == 1 and report['reconciled'] == 1

    def test_reconciled_is_not_a_closure(self, session):
        """WARNING: The design document called `isReconciled` the closure signal.
        It is not: the local smoke measured **9 of 9** worklist rows reconciled
        in XRAS while every one still needed a SAM account created or
        reactivated. Reconciliation is XRAS linking a placeholder to a real
        identity; it says nothing about whether SAM has a row.

        The row must therefore SURVIVE enrichment — the thing that closes it is
        the `users` row appearing, which classification checks every render."""
        rows = classify_accounts(session, [_record('ghost-user-1')])
        report = enrich_worklist(
            rows, person_lookup=lambda u: {'isReconciled': True})
        assert report['reconciled'] == 1
        assert rows[0]['classification'] == 'absent', \
            'a reconciled identity is still an absent SAM account'
        assert 'closed' not in report

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


class TestItIsRegimeProof:
    """§ 1's requirement, and the one the first implementation broke.

    Actions in the log are `received` under capture-only and
    `processed`/`failed`/`manual` under live dispatch. The worklist must mean
    the same thing on both sides of that flip — classification already keys
    off the `users` table alone, but a narrow status pre-filter reintroduced
    the dependency behind it.

    Measured on the local smoke: 41 real payloads gave **9 rows as `received`
    and 4 once 28 of them had dispatched**, hiding four inactive users and one
    absent Allocation Manager.
    """

    def test_every_status_is_read(self):
        """A processed action still names people SAM may not be able to use:
        `resolve_roster` reports a missing or inactive *member* as a warning,
        not an error, so the handler skips assigning them and succeeds."""
        from sam.queries.xras_actions import XRAS_ACTION_STATUSES

        from sam.queries.xras_accounts import WORKLIST_STATUSES

        assert set(WORKLIST_STATUSES) == set(XRAS_ACTION_STATUSES), (
            'narrowing WORKLIST_STATUSES makes the worklist regime-dependent')

    def test_a_processed_action_still_yields_its_roster(self, session):
        """The concrete case the smoke found."""
        import json
        from datetime import datetime as dt

        from sam.integration.xras import XrasActionLog

        payload = json.loads((FIXTURES / PLACEHOLDER_FIXTURE).read_text())
        session.add(XrasActionLog(received_time=dt(2026, 8, 1),
                                  remote_actor='XRAS',
                                  raw_payload=json.dumps(payload),
                                  status='processed'))
        session.flush()
        records = records_from_action_log(session, validate=False)
        assert records, 'a processed action was skipped'
        rows = classify_accounts(session, records)
        assert PLACEHOLDER_USERNAME in {r['username'] for r in rows}

    def test_the_same_roster_classifies_alike_in_every_regime(
            self, session, no_committed_placeholder):
        """Flip only the status; the answer must not move."""
        import json
        from datetime import datetime as dt

        from sam.integration.xras import XrasActionLog

        payload = json.loads((FIXTURES / PLACEHOLDER_FIXTURE).read_text())
        answers = set()
        for status in ('received', 'processed', 'failed', 'manual',
                       'rechecked', 'unmapped'):
            row = XrasActionLog(received_time=dt(2026, 8, 1),
                                remote_actor='XRAS',
                                raw_payload=json.dumps(payload), status=status)
            session.add(row)
            session.flush()
            rows = classify_accounts(
                session, records_from_action_log(session, validate=False))
            answers.add(next(r['classification'] for r in rows
                             if r['username'] == PLACEHOLDER_USERNAME))
            session.delete(row)
            session.flush()
        assert answers == {'absent'}

    def test_the_preflight_is_what_stays_bounded(self, session):
        """Validation is the expensive part, and skipping it on a processed
        action costs provenance — never a classification."""
        import json
        from datetime import datetime as dt

        from sam.integration.xras import XrasActionLog
        from sam.queries.xras_accounts import VALIDATE_STATUSES

        assert 'processed' not in VALIDATE_STATUSES

        payload = json.loads((FIXTURES / PLACEHOLDER_FIXTURE).read_text())
        row = XrasActionLog(received_time=dt(2026, 8, 1), remote_actor='XRAS',
                            raw_payload=json.dumps(payload), status='processed')
        session.add(row)
        session.flush()
        records = records_from_action_log(session, validate=True)
        mine = next(r for r in records
                    if r.ref.action_log_id == row.xras_action_log_id)
        assert mine.ref.would_succeed is None            # not run...
        rows = classify_accounts(session, [mine])        # ...and still classified
        assert PLACEHOLDER_USERNAME in {r['username'] for r in rows}


class TestUsernameCaseFolding:
    """WARNING: Found by the local smoke against real data; unreachable from fixtures.

    `users.username` is `utf8mb3_general_ci` with a UNIQUE index, so MySQL
    treats `Jsmith` and `jsmith` as one account and the batch `IN` matches
    either spelling. A case-sensitive dict on top of that misses the row it was
    just handed and reports `absent` — telling an operator to create an account
    that already exists and is active.

    No in-tree fixture can reach this: the anonymizer rewrites every username
    to lowercase `user_<hex>`, so every scrubbed roster is already
    case-matched. `roster.normalize_username` does not fold case either (it
    reproduces Java), so the wire spelling arrives untouched.
    """

    def test_a_case_mismatched_active_user_is_not_reported(self, session):
        """The exact false positive: XRAS sent `Jsmith`, SAM holds `jsmith`."""
        user = make_user(session, username='casefoldcheck', active=True)
        rows = classify_accounts(
            session, [_record(user.username.upper())])
        assert rows == [], (
            'a case-mismatched ACTIVE account was reported as needing creation')

    def test_a_case_mismatched_inactive_user_is_inactive_not_absent(self, session):
        """The remedy differs: reactivate, not create."""
        user = make_user(session, username='casefoldinactive', active=False)
        rows = classify_accounts(session, [_record(user.username.upper())])
        assert [r['classification'] for r in rows] == ['inactive']

    def test_two_spellings_are_one_row_of_work(self, session):
        """One account, one operator task — not two."""
        rows = classify_accounts(session, [
            _record('GhostCaseUser'), _record('ghostcaseuser')])
        assert len(rows) == 1
        # Both actions are attached to the single row.
        assert len(rows[0]['actions']) == 2

    def test_the_wire_spelling_is_preserved_for_display(self, session):
        """For an absent account it is the only spelling there is, and for a
        present one the mismatch is itself worth seeing."""
        rows = classify_accounts(session, [_record('GhostCaseUser')])
        assert rows[0]['username'] == 'GhostCaseUser'


class TestWaitingSince:
    """How long a row has been blocking something.

    WARNING: **Neither feed alone answers this**, which is the whole reason it is
    derived rather than read off a column. Feed A knows ``received_time`` —
    when XRAS pushed the action at us — and leaves ``submit_date`` null. Feed B
    is the exact inverse: a request that has not been pushed has no arrival,
    only the ``submitDate`` it got in XRAS.
    """

    def test_feed_a_dates_from_when_the_action_arrived(self, session):
        rows = classify_accounts(session, [_record('ghostwaiting')])
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_since'] is not None

    def test_feed_b_dates_from_the_xras_submit_date(self, session):
        """A pending request has no arrival of its own — only a submitDate."""
        rows = classify_accounts(session, [_pending_record(
            'ghostpending', submit_date='2026-07-14')])
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_since'] == date(2026, 7, 14)
        assert rows[0]['waiting_days'] == 37

    def test_the_earliest_signal_wins_across_feeds(self, session):
        """A person on both feeds has been waiting since the EARLIER of them,
        not since whichever feed happened to notice second."""
        rows = classify_accounts(session, [
            _pending_record('ghostboth', submit_date='2026-07-14'),
            _pending_record('ghostboth', submit_date='2026-08-01')])
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_since'] == date(2026, 7, 14)

    def test_a_negative_age_is_clamped_not_rendered(self, session):
        """WARNING: Clock skew, not a fact about the queue.

        ``received_time`` is naive-Mountain from the app clock, so a process
        running in another zone stamps rows that read as the future — a
        container with no TZ set does exactly this, six hours ahead of the data
        it is writing. "-1d waiting" is a worse thing to render than "0d".
        """
        rows = classify_accounts(session, [_pending_record(
            'ghostfuture', submit_date='2026-08-25')])
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_days'] == 0

    def test_an_undatable_row_has_no_age_rather_than_a_wrong_one(self, session):
        rows = classify_accounts(session, [_pending_record(
            'ghostnodate', submit_date=None)])
        for row in rows:
            row['first_seen'] = None
            row['waiting_since'] = None
            row['actions'] = [dict(a, received_time=None) for a in row['actions']]
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_days'] is None

    def test_a_snapshot_written_by_older_code_is_backfilled(self, session):
        """WARNING: The publisher and the reader can be on different code.

        Feed B is read back from a snapshot ``xras_sweep`` wrote; mid-deploy the
        task is guaranteed to be older than the reader, and a cached snapshot
        outlives a rollback. A row with no ``waiting_since`` is version skew,
        not a row with no age.
        """
        rows = classify_accounts(session, [_pending_record(
            'ghostskew', submit_date='2026-07-14')])
        del rows[0]['waiting_since']          # as an older publisher left it
        stamp_waiting_days(rows, today=date(2026, 8, 20))
        assert rows[0]['waiting_days'] == 37


class TestMergeWorklists:
    """The union behind ``sam-admin xras --accounts``.

    WARNING: **Overlap is normal.** Feed A is precisely the actions that have
    *posted*; Feed B is what XRAS approved and *may or may not* have posted. The
    same person legitimately appears in both, so this is a union on the
    casefolded username, not a concatenation.
    """

    def test_a_person_on_both_feeds_is_one_row_of_work(self):
        a = [{'username': 'Ghost', 'classification': 'absent',
              'roles': ('PI',), 'actions': [{'request_number': 'NCAR0001'}],
              'sources': ['action_log'], 'first_seen': None, 'last_seen': None,
              'waiting_since': date(2026, 8, 1), 'person': None,
              'is_reconciled': None, 'latest_action_log_id': 7}]
        b = [{'username': 'ghost', 'classification': 'absent',
              'roles': ('User',), 'actions': [{'request_number': 'NCAR0002'}],
              'sources': ['reports'], 'first_seen': None, 'last_seen': None,
              'waiting_since': date(2026, 7, 1), 'person': {'lastName': 'X'},
              'is_reconciled': True, 'latest_action_log_id': None}]
        merged = merge_worklists(a, b)
        assert len(merged) == 1
        row = merged[0]
        # Provenance is unioned — a person can be a PI on a posted action and a
        # User on a pending one, and losing either misreports why they block.
        assert set(row['roles']) == {'PI', 'User'}
        assert row['sources'] == ['action_log', 'reports']
        assert len(row['actions']) == 2
        # Earliest wins: the question is how long they have waited, not which
        # feed noticed first.
        assert row['waiting_since'] == date(2026, 7, 1)
        # Detail rides whichever feed carried it.
        assert row['person'] == {'lastName': 'X'}
        assert row['is_reconciled'] is True
        assert row['latest_action_log_id'] == 7

    def test_disjoint_feeds_concatenate(self):
        a = [{'username': 'one', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['action_log'], 'waiting_since': None}]
        b = [{'username': 'two', 'classification': 'inactive', 'roles': (),
              'actions': [], 'sources': ['reports'], 'waiting_since': None}]
        assert len(merge_worklists(a, b)) == 2

    def test_absent_still_sorts_before_inactive(self):
        a = [{'username': 'zzz', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': [], 'waiting_since': None}]
        b = [{'username': 'aaa', 'classification': 'inactive', 'roles': (),
              'actions': [], 'sources': [], 'waiting_since': None}]
        assert [r['username'] for r in merge_worklists(a, b)] == ['zzz', 'aaa']

    def test_received_push_rows_lead(self):
        # A Feed-A row is the more urgent flavor -- a push already arrived and is
        # blocked -- so it leads even a lexically earlier pending-only row.
        a = [{'username': 'zzz_push', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['action_log'], 'waiting_since': None}]
        b = [{'username': 'aaa_pending', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['reports'], 'waiting_since': None}]
        assert [r['username'] for r in merge_worklists(a, b)] == [
            'zzz_push', 'aaa_pending']

    def test_the_merge_copies_action_dicts_it_does_not_alias(self):
        # stamp_project_existence writes action['is_project'] in place; the
        # primary rows are the cached snapshot's own objects, so the merge must
        # copy each action or the mutation leaks into the next render.
        pending_action = {'request_number': 'NCAR0002'}
        b = [{'username': 'ghost', 'classification': 'absent', 'roles': (),
              'actions': [pending_action], 'sources': ['reports'],
              'waiting_since': None}]
        merged = merge_worklists([], b)
        merged[0]['actions'][0]['is_project'] = True
        assert 'is_project' not in pending_action

    def test_counts_split_by_source(self):
        rows = merge_worklists(
            [{'username': 'a', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['action_log'], 'waiting_since': None,
              'placeholder': False, 'is_reconciled': None}],
            [{'username': 'a', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['reports'], 'waiting_since': None,
              'placeholder': False, 'is_reconciled': None},
             {'username': 'b', 'classification': 'absent', 'roles': (),
              'actions': [], 'sources': ['reports'], 'waiting_since': None,
              'placeholder': False, 'is_reconciled': None}])
        counts = worklist_counts(rows)
        # A row on both feeds counts in both.
        assert counts['received_push'] == 1
        assert counts['pending_request'] == 2


class TestLoadPendingWorklistRows:
    """The three degraded states are distinct facts, not one empty list."""

    def _patch(self, monkeypatch, *, configured=True, loader=None):
        monkeypatch.setattr('sam.integration.xras_api.xras_api_configured',
                            lambda: configured)
        if loader is not None:
            monkeypatch.setattr(
                'sam.integration.xras_api.cache.load_pending_worklist', loader)

    def test_unconfigured(self, monkeypatch):
        self._patch(monkeypatch, configured=False)
        feed = load_pending_worklist_rows()
        assert feed == PendingFeed(reason='unconfigured')
        assert feed.checked is False and feed.rows == []

    def test_no_snapshot(self, monkeypatch):
        self._patch(monkeypatch, loader=lambda: None)
        feed = load_pending_worklist_rows()
        assert feed.reason == 'no_snapshot' and feed.checked is False

    def test_unreadable_never_raises(self, monkeypatch):
        self._patch(monkeypatch,
                    loader=lambda: (_ for _ in ()).throw(RuntimeError('no redis')))
        feed = load_pending_worklist_rows()
        assert feed.reason == 'unreadable' and feed.checked is False

    def test_success_carries_rows_and_snapshot(self, monkeypatch):
        snap = {'rows': [{'username': 'x'}], 'generated_at': None}
        self._patch(monkeypatch, loader=lambda: snap)
        feed = load_pending_worklist_rows()
        assert feed.checked is True and feed.reason is None
        assert feed.rows == [{'username': 'x'}] and feed.snapshot is snap
