"""The write side: invite, roster paste, the sweep feed, and reconcile.

Happy paths live here rather than at the HTTP layer, per the house rule.
The rows are built by factories and rolled back by the per-test SAVEPOINT.
"""

from datetime import datetime, timedelta

import pytest
from factories import (
    make_account,
    make_account_request,
    make_account_request_event,
    make_email_address,
    make_project,
    make_user,
)

from sam.accounting.accounts import AccountUser
from sam.core.account_requests import (
    CREATED_BY_SELF, CREATED_BY_SWEEP, AccountRequest, EventEnrollment,
)
from sam.manage.account_requests import (
    OUTCOME_ADDED,
    OUTCOME_DUPLICATE,
    OUTCOME_QUEUED,
    enroll_user_in_event,
    invite_user,
    parse_roster,
    paste_roster,
    reconcile_account_requests,
    upsert_sweep_requests,
)
from sam.queries.account_requests import queue_requests

pytestmark = pytest.mark.unit


def _project_with_account(session):
    project = make_project(session)
    make_account(session, project=project)
    return project


def _user_with_email(session, email, *, active=True):
    user = make_user(session, active=active)
    make_email_address(session, user, email=email)
    return user


def _memberships(session, project, user):
    return (session.query(AccountUser)
            .join(AccountUser.account)
            .filter(AccountUser.user_id == user.user_id)
            .filter(AccountUser.account.has(project_id=project.project_id))
            .count())


class TestInviteUser:
    def test_a_known_active_address_becomes_a_member_at_once(self, session):
        project = _project_with_account(session)
        sponsor = make_user(session)
        known = _user_with_email(session, 'known@example.edu')
        outcome, user = invite_user(
            session, project_id=project.project_id, sponsor=sponsor,
            email='Known@Example.edu', first_name='K', last_name='Nown')
        assert outcome == OUTCOME_ADDED and user.user_id == known.user_id
        assert _memberships(session, project, known) == 1
        assert session.query(AccountRequest).filter_by(email='known@example.edu').count() == 0

    def test_an_unknown_address_is_queued_vouched_by_the_sponsor(self, session):
        project = _project_with_account(session)
        sponsor = make_user(session)
        event = make_account_request_event(session, project=project)
        outcome, row = invite_user(
            session, project_id=project.project_id, sponsor=sponsor,
            email='new@example.edu', first_name='N', last_name='Ew',
            note='visiting scholar', event=event)
        assert outcome == OUTCOME_QUEUED
        assert row.purpose == 'enrollment' and row.project_id == project.project_id
        assert row.sponsor_user_id == sponsor.user_id
        assert row.event_id == event.account_request_event_id
        assert row.created_by == sponsor.username and row.verified_by == sponsor.username
        assert row.is_open and row.comment == 'visiting scholar'

    def test_an_inactive_holder_is_queued_with_the_account_named(self, session):
        project = _project_with_account(session)
        locked = _user_with_email(session, 'locked@example.edu', active=False)
        outcome, row = invite_user(
            session, project_id=project.project_id, sponsor=make_user(session),
            email='locked@example.edu', first_name='L', last_name='Ocked')
        assert outcome == OUTCOME_QUEUED
        assert locked.username in row.comment
        assert _memberships(session, project, locked) == 0

    def test_two_active_holders_is_refused(self, session):
        _user_with_email(session, 'shared@example.edu')
        _user_with_email(session, 'shared@example.edu')
        with pytest.raises(ValueError, match='more than one'):
            invite_user(session, project_id=_project_with_account(session).project_id,
                        sponsor=make_user(session), email='shared@example.edu',
                        first_name='S', last_name='Hared')

    def test_a_second_invite_for_the_same_address_and_project_is_a_duplicate(self, session):
        project = _project_with_account(session)
        sponsor = make_user(session)
        kwargs = dict(session=session, project_id=project.project_id, sponsor=sponsor,
                      email='dup@example.edu', first_name='D', last_name='Up')
        _, first = invite_user(**kwargs)
        outcome, again = invite_user(**kwargs)
        assert outcome == OUTCOME_DUPLICATE and again is first

    def test_the_same_address_on_another_project_is_a_new_row(self, session):
        sponsor = make_user(session)
        for project in (_project_with_account(session), _project_with_account(session)):
            outcome, _ = invite_user(
                session, project_id=project.project_id, sponsor=sponsor,
                email='twice@example.edu', first_name='T', last_name='Wice')
            assert outcome == OUTCOME_QUEUED


class TestParseRoster:
    def test_the_accepted_shapes(self):
        entries, errors = parse_roster(
            '# workshop\n'
            'Ada Lovelace <ada@example.edu>\n'
            'Turing, Alan <ALAN@example.edu>\n'
            '"Grace Brewster Hopper" <grace@example.edu>\n'
            'Linus Torvalds linus@example.edu\n'
            '\n')
        assert errors == []
        assert entries == [
            {'email': 'ada@example.edu', 'first_name': 'Ada', 'last_name': 'Lovelace'},
            {'email': 'alan@example.edu', 'first_name': 'Alan', 'last_name': 'Turing'},
            {'email': 'grace@example.edu', 'first_name': 'Grace Brewster', 'last_name': 'Hopper'},
            {'email': 'linus@example.edu', 'first_name': 'Linus', 'last_name': 'Torvalds'},
        ]

    def test_errors_name_their_line_and_good_lines_survive(self):
        entries, errors = parse_roster(
            'Ada Lovelace <ada@example.edu>\n'
            'bare@example.edu\n'
            'Cher <cher@example.edu>\n'
            'Ada Again <ADA@example.edu>\n'
            'no address at all\n')
        assert [e['email'] for e in entries] == ['ada@example.edu']
        assert errors == [
            'line 2: expected "Name <email>"',
            'line 3: a first and last name are required',
            'line 4: duplicate of line 1',
            'line 5: expected "Name <email>"',
        ]


class TestPasteRoster:
    def test_outcomes_are_reported_per_address(self, session):
        project = _project_with_account(session)
        event = make_account_request_event(session, project=project)
        sponsor = make_user(session)
        known = _user_with_email(session, 'known@example.edu')
        _user_with_email(session, 'shared@example.edu')
        _user_with_email(session, 'shared@example.edu')
        make_account_request(session, purpose='enrollment', project=project,
                             email='dup@example.edu')
        entries, _ = parse_roster(
            'K Nown <known@example.edu>\nN Ew <new@example.edu>\n'
            'D Up <dup@example.edu>\nS Hared <shared@example.edu>\n')
        outcomes = paste_roster(session, event=event, sponsor=sponsor, entries=entries)
        assert outcomes[OUTCOME_ADDED] == ['known@example.edu']
        assert outcomes[OUTCOME_QUEUED] == ['new@example.edu']
        assert outcomes[OUTCOME_DUPLICATE] == ['dup@example.edu']
        assert [e for e, _ in outcomes['error']] == ['shared@example.edu']
        assert _memberships(session, project, known) == 1
        queued = session.query(AccountRequest).filter_by(email='new@example.edu').one()
        assert queued.event_id == event.account_request_event_id


def _worklist_row(username, *, classification='absent', email=None,
                  first='First', last='Last', request_number=None):
    return {
        'username': username,
        'classification': classification,
        'person': {'email': email, 'firstName': first, 'lastName': last,
                   'organization': 'NCAR', 'residenceCountry': 'US'},
        'actions': [{'request_number': request_number}] if request_number else [],
    }


class TestUpsertSweepRequests:
    def test_absent_rows_become_submission_requests_once(self, session):
        project = make_project(session)
        rows = [_worklist_row('Jane-User-ABC', email='jane@example.edu',
                              request_number=project.projcode),
                _worklist_row('john-user-def', email='john@example.edu')]
        assert upsert_sweep_requests(session, rows) == {'created': 2, 'existing': 0, 'skipped': 0}
        jane = session.query(AccountRequest).filter_by(xras_username='jane-user-abc').one()
        assert jane.purpose == 'submission' and jane.project_id == project.project_id
        assert jane.created_by == CREATED_BY_SWEEP and jane.is_open
        assert jane.organization == 'NCAR' and jane.residence_country == 'US'
        assert upsert_sweep_requests(session, rows) == {'created': 0, 'existing': 2, 'skipped': 0}

    def test_a_dismissed_row_survives_the_next_sweep(self, session):
        rows = [_worklist_row('Gone-user-1', email='gone@example.edu')]
        upsert_sweep_requests(session, rows)
        session.query(AccountRequest).filter_by(xras_username='gone-user-1').one().dismiss('op', 'dup')
        assert upsert_sweep_requests(session, [_worklist_row('gone-user-1', email='gone@example.edu')]
                                     ) == {'created': 0, 'existing': 1, 'skipped': 0}

    def test_inactive_rows_and_rows_without_a_person_are_not_taken(self, session):
        rows = [_worklist_row('locked-user', classification='inactive', email='l@example.edu'),
                _worklist_row('noemail-user', email=None),
                _worklist_row('noname-user', email='n@example.edu', first='')]
        assert upsert_sweep_requests(session, rows) == {'created': 0, 'existing': 0, 'skipped': 2}


class TestReconcile:
    def test_a_row_is_fulfilled_when_the_mirror_catches_up(self, session):
        row = make_account_request(session, email='arrived@example.edu')
        assert reconcile_account_requests(session)['fulfilled'] == 0
        user = _user_with_email(session, 'arrived@example.edu')
        counts = reconcile_account_requests(session, clock=datetime(2026, 9, 16, 9, 0))
        assert counts['fulfilled'] == 1 and counts['enrolled'] == 0
        assert row.user_id == user.user_id and row.upid == user.upid
        assert row.fulfilled_at == datetime(2026, 9, 16, 9, 0)
        assert row.account_request_id not in {r.account_request_id
                                              for r in queue_requests(session)}

    def test_an_enrollment_adds_the_membership(self, session):
        project = _project_with_account(session)
        row = make_account_request(session, purpose='enrollment', project=project,
                                   email='enroll@example.edu')
        user = _user_with_email(session, 'enroll@example.edu')
        counts = reconcile_account_requests(session)
        assert counts['enrolled'] == 1 and counts['enroll_failed'] == 0
        assert _memberships(session, project, user) == 1
        assert row.fulfill_error is None

    def test_a_failed_enrollment_is_recorded_and_retried(self, session):
        project = make_project(session)  # no accounts: add_user_to_project raises
        row = make_account_request(session, purpose='enrollment', project=project,
                                   email='fail@example.edu')
        user = _user_with_email(session, 'fail@example.edu')
        counts = reconcile_account_requests(session)
        assert counts == {'checked': counts['checked'], 'fulfilled': 1, 'enrolled': 0,
                          'enroll_failed': 1, 'ambiguous': 0, 'purged': 0}
        assert row.user_id == user.user_id, 'the account exists; that is stamped'
        assert 'No accounts' in row.fulfill_error
        assert row.account_request_id in {r.account_request_id for r in queue_requests(session)}
        make_account(session, project=project)
        counts = reconcile_account_requests(session)
        assert counts['enrolled'] == 1 and counts['fulfilled'] == 0
        assert row.fulfill_error is None
        assert _memberships(session, project, user) == 1

    def test_ambiguous_and_inactive_are_left_alone(self, session):
        _user_with_email(session, 'amb@example.edu')
        _user_with_email(session, 'amb@example.edu')
        _user_with_email(session, 'inactive@example.edu', active=False)
        amb = make_account_request(session, email='amb@example.edu')
        inactive = make_account_request(session, email='inactive@example.edu')
        counts = reconcile_account_requests(session)
        assert counts['ambiguous'] == 1 and counts['fulfilled'] == 0
        assert amb.user_id is None and inactive.user_id is None

    def test_stale_unverified_public_rows_are_purged_and_nothing_else(self, session):
        now = datetime(2026, 9, 16, 12, 0)
        stale = make_account_request(session, by=CREATED_BY_SELF, verified_by=None,
                                     when=now - timedelta(days=8))
        fresh = make_account_request(session, by=CREATED_BY_SELF, verified_by=None,
                                     when=now - timedelta(days=6))
        vouched = make_account_request(session, when=now - timedelta(days=30))
        counts = reconcile_account_requests(session, clock=now, purge_unverified_days=7)
        assert counts['purged'] == 1
        ids = {r.account_request_id for r in session.query(AccountRequest).all()}
        assert stale.account_request_id not in ids
        assert {fresh.account_request_id, vouched.account_request_id} <= ids


def _enrollments(session, event, user):
    return (session.query(EventEnrollment)
            .filter_by(event_id=event.account_request_event_id,
                       user_id=user.user_id).count())


class TestEventEnrollmentLedger:
    """Every enrollment path records the event<->user tie exactly once."""

    def test_enroll_adds_membership_and_one_ledger_row(self, session):
        project = _project_with_account(session)
        event = make_account_request_event(session, project=project)
        user = make_user(session)
        enroll_user_in_event(session, event=event, user=user, source='self',
                             by=user.username)
        assert _memberships(session, project, user) == 1
        assert _enrollments(session, event, user) == 1

    def test_enroll_is_idempotent(self, session):
        project = _project_with_account(session)
        event = make_account_request_event(session, project=project)
        user = make_user(session)
        for _ in range(2):
            enroll_user_in_event(session, event=event, user=user, source='self',
                                 by=user.username)
        assert _enrollments(session, event, user) == 1

    def test_existing_user_invite_with_event_records_enrollment(self, session):
        """OUTCOME_ADDED used to leave no event trace; now it enrolls."""
        project = _project_with_account(session)
        sponsor = make_user(session)
        event = make_account_request_event(session, project=project)
        known = _user_with_email(session, 'known.enrol@example.edu')
        outcome, _ = invite_user(
            session, project_id=project.project_id, sponsor=sponsor,
            email='known.enrol@example.edu', first_name='K', last_name='Nown',
            event=event)
        assert outcome == OUTCOME_ADDED
        assert _enrollments(session, event, known) == 1

    def test_existing_user_invite_without_event_records_nothing(self, session):
        project = _project_with_account(session)
        sponsor = make_user(session)
        _user_with_email(session, 'noevent@example.edu')
        invite_user(session, project_id=project.project_id, sponsor=sponsor,
                    email='noevent@example.edu', first_name='N', last_name='One')
        assert session.query(EventEnrollment).count() == 0

    def test_reconcile_records_enrollment_for_an_event_row(self, session):
        project = _project_with_account(session)
        event = make_account_request_event(session, project=project)
        user = _user_with_email(session, 'queued@example.edu')
        make_account_request(session, email='queued@example.edu',
                             purpose='enrollment', project=project, event=event,
                             first_name='Q', last_name='Ueue')
        reconcile_account_requests(session)
        assert _enrollments(session, event, user) == 1
