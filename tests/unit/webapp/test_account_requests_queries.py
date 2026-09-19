"""The derive-only read side of the account-request queue.

Nothing here writes to a request row; what is pinned is that the email is
the only key that resolves, that ``ambiguous`` is reported rather than
guessed, that a ``desired_username`` hit is a hint and nothing more, and the
grouping and stamping shapes the cards read.
"""

from datetime import date, datetime, timedelta

from factories import (
    make_account_request,
    make_account_request_event,
    make_email_address,
    make_event_enrollment,
    make_project,
    make_user,
)

from sam.projects.projects import Project
from sam.queries.account_requests import (
    Resolution,
    all_events,
    all_requests,
    enrolled_event_ids,
    enrollees_for_event,
    events_for,
    events_for_user,
    group_by_event,
    queue_counts,
    queue_requests,
    readiness_of,
    request_views,
    resolve_requests,
    stamp_account_requests,
    unverified_count,
    upcoming_listed_events,
    user_has_enrollments,
    waiting_days,
)



def _user_with_email(session, email, *, active=True):
    user = make_user(session, active=active)
    make_email_address(session, user, email=email)
    return user


class TestResolveRequests:
    def test_an_active_holder_resolves_ready(self, session):
        user = _user_with_email(session, 'ready@example.edu')
        row = make_account_request(session, email='Ready@Example.edu')
        res = resolve_requests(session, [row])[row.account_request_id]
        assert res.ready and res.user_id == user.user_id
        assert res.username == user.username
        assert readiness_of(row, res) == 'ready'

    def test_an_inactive_holder_is_reported_not_ready(self, session):
        user = _user_with_email(session, 'locked@example.edu', active=False)
        row = make_account_request(session, email='locked@example.edu')
        res = resolve_requests(session, [row])[row.account_request_id]
        assert res.username == user.username and not res.active and not res.ready
        assert readiness_of(row, res) == 'inactive'

    def test_two_active_holders_are_ambiguous_not_guessed(self, session):
        _user_with_email(session, 'shared@example.edu')
        _user_with_email(session, 'shared@example.edu')
        row = make_account_request(session, email='shared@example.edu')
        res = resolve_requests(session, [row])[row.account_request_id]
        assert res.ambiguous and res.user_id is None
        assert readiness_of(row, res) == 'ambiguous'

    def test_an_unknown_address_has_no_resolution(self, session):
        row = make_account_request(session)
        assert resolve_requests(session, [row]) == {}
        assert readiness_of(row, None) == 'open'

    def test_desired_username_is_a_hint_never_a_match(self, session):
        stranger = make_user(session)
        row = make_account_request(session, desired_username=stranger.username.upper())
        res = resolve_requests(session, [row])[row.account_request_id]
        assert res.hint == stranger.username, 'casefolded hit is reported'
        assert res.user_id is None and not res.ready, 'and never acted on'

    def test_a_fulfilled_row_reads_fulfilled_whatever_the_mirror_says(self, session):
        user = _user_with_email(session, 'done@example.edu')
        row = make_account_request(session, email='done@example.edu').fulfill(user)
        assert readiness_of(row, Resolution()) == 'fulfilled'
        row.record_fulfill_error('no accounts')
        assert readiness_of(row, None) == 'failed'


class TestTheQueuePredicate:
    def test_only_open_verified_unfulfilled_rows_are_in_the_queue(self, session):
        shown = make_account_request(session)
        make_account_request(session, verified_by=None)
        make_account_request(session).dismiss('op', 'dup')
        user = make_user(session)
        make_account_request(session).fulfill(user)
        ids = {r.account_request_id for r in queue_requests(session)}
        assert shown.account_request_id in ids
        assert len(ids & {shown.account_request_id}) == 1
        assert all(r.is_open and r.user_id is None or r.fulfill_error
                   for r in queue_requests(session))

    def test_a_failed_enrollment_stays_in_the_queue(self, session):
        user = make_user(session)
        row = make_account_request(session).fulfill(user).record_fulfill_error('x')
        assert row.account_request_id in {r.account_request_id
                                          for r in queue_requests(session)}

    def test_everything_is_newest_first(self, session):
        older = make_account_request(session, when=datetime(2026, 1, 1))
        newer = make_account_request(session, when=datetime(2026, 6, 1))
        ids = [r.account_request_id for r in all_requests(session)]
        assert ids.index(newer.account_request_id) < ids.index(older.account_request_id)

    def test_unverified_count_sees_only_unverified_open_rows(self, session):
        before = unverified_count(session)
        make_account_request(session, verified_by=None)
        make_account_request(session, verified_by=None).dismiss('op', 'spam')
        assert unverified_count(session) == before + 1

    def test_waiting_days_is_clamped(self, session):
        row = make_account_request(session, when=datetime(2026, 9, 1))
        assert waiting_days(row, today=date(2026, 9, 11)) == 10
        assert waiting_days(row, today=date(2026, 8, 1)) == 0


class TestGrouping:
    def _views(self, session, rows):
        return request_views(session, rows, resolutions=resolve_requests(session, rows),
                             events=events_for(session, rows))

    def test_events_nearest_deadline_first_then_loose_rows_in_input_order(self, session):
        late = make_account_request_event(session, accounts_needed_by=date(2026, 12, 1))
        soon = make_account_request_event(session, accounts_needed_by=date(2026, 10, 1))
        a = make_account_request(session, purpose='enrollment', event=late)
        b = make_account_request(session, purpose='enrollment', event=soon)
        loose_new = make_account_request(session, when=datetime(2026, 9, 2))
        loose_old = make_account_request(session, when=datetime(2026, 9, 1))
        rows = [a, loose_old, b, loose_new]
        groups = group_by_event(self._views(session, rows))
        assert [g['event'] and g['event'].event_code for g in groups] == [
            soon.event_code, late.event_code, None]
        assert [v['id'] for v in groups[-1]['rows']] == [
            loose_old.account_request_id, loose_new.account_request_id]
        assert groups[0]['project_code'] == session.get(Project, soon.project_id).projcode

    def test_event_less_rows_split_by_project_with_project_less_last(self, session):
        p1, p2 = make_project(session), make_project(session)
        rows = [make_account_request(session),
                make_account_request(session, purpose='enrollment', project=p2),
                make_account_request(session, purpose='enrollment', project=p1),
                make_account_request(session, purpose='enrollment', project=p2)]
        groups = group_by_event(self._views(session, rows))
        assert all(g['event'] is None for g in groups)
        assert [g['project_code'] for g in groups] == sorted([p1.projcode, p2.projcode]) + ['']
        by_code = {g['project_code']: [v['id'] for v in g['rows']] for g in groups}
        assert by_code[p2.projcode] == [rows[1].account_request_id, rows[3].account_request_id]

    def test_a_row_naming_a_vanished_event_is_ungrouped_not_lost(self, session):
        row = make_account_request(session, purpose='enrollment', event_id=999_999_999,
                                   project=make_account_request_event(session).project_id)
        view, = self._views(session, [row])
        assert view['event'] is None and view['event_code'] == ''
        assert group_by_event([view]) == [
            {'event': None, 'rows': [view], 'project_code': view['project_code']}]

    def test_views_carry_the_sponsor_and_both_project_codes(self, session):
        sponsor = make_user(session)
        event = make_account_request_event(session)
        row = make_account_request(session, purpose='enrollment', event=event,
                                   sponsor=sponsor)
        view, = self._views(session, [row])
        assert view['sponsor'].user_id == sponsor.user_id
        assert view['project_code'] == view['event_project_code'] != ''
        assert view['origin'] == 'sponsor' and view['readiness'] == 'open'
        assert view['verified'] is True and view['deadline'] == event.accounts_needed_by

    def test_queue_counts(self, session):
        user = _user_with_email(session, 'c@example.edu')
        rows = [make_account_request(session, email='c@example.edu'),
                make_account_request(session).claim('op'),
                make_account_request(session).fulfill(user).record_fulfill_error('x')]
        counts = queue_counts(rows, resolve_requests(session, rows))
        assert counts == {'open': 2, 'claimed': 1, 'ready': 1, 'failed': 1}


class TestStampAccountRequests:
    def test_worklist_rows_point_at_their_request_casefolded(self, session):
        row = make_account_request(session, purpose='submission',
                                   xras_username='Jane-user-ABC123').claim('op')
        assert row.xras_username == 'jane-user-abc123', 'the stored key is lower-case'
        worklist = [{'username': 'JANE-user-abc123'}, {'username': 'nobody-user-x'}]
        stamp_account_requests(session, worklist)
        assert worklist[0]['account_request'] == {
            'id': row.account_request_id, 'state': 'claimed', 'assignee': 'op',
            'requested_at': None, 'fulfilled': False}
        assert worklist[1]['account_request'] is None

    def test_an_empty_worklist_is_a_no_op(self, session):
        stamp_account_requests(session, [])


class TestEnrollmentReads:
    """The event<->user ledger, read both directions."""

    def test_enrollees_for_event_lists_each_user_newest_first(self, session):
        event = make_account_request_event(session)
        first = make_user(session)
        second = make_user(session)
        make_event_enrollment(session, event=event, user=first, source='invite',
                              when=datetime(2026, 1, 1))
        make_event_enrollment(session, event=event, user=second, source='self',
                              when=datetime(2026, 2, 1))
        rows = enrollees_for_event(session, event.account_request_event_id)
        assert [r['user'].user_id for r in rows] == [second.user_id, first.user_id]
        assert rows[0]['source'] == 'self'

    def test_events_for_user_carries_the_project_code(self, session):
        user = make_user(session)
        event = make_account_request_event(session)
        make_event_enrollment(session, event=event, user=user)
        rows = events_for_user(session, user.user_id)
        assert len(rows) == 1
        project = session.get(Project, event.project_id)
        assert rows[0]['event'].account_request_event_id == event.account_request_event_id
        assert rows[0]['project_code'] == project.projcode

    def test_user_has_enrollments_is_a_cheap_exists(self, session):
        user = make_user(session)
        assert user_has_enrollments(session, user.user_id) is False
        make_event_enrollment(session, user=user)
        assert user_has_enrollments(session, user.user_id) is True


class TestCrossProjectEventReads:
    """Admin -> Events and the public Upcoming Events card."""

    def test_all_events_carries_project_code_and_enrollee_count(self, session):
        event = make_account_request_event(session)
        make_event_enrollment(session, event=event, user=make_user(session))
        make_event_enrollment(session, event=event, user=make_user(session))
        row = next(r for r in all_events(session)
                   if r['event'].account_request_event_id == event.account_request_event_id)
        assert row['enrolled'] == 2
        assert row['project_code'] == session.get(Project, event.project_id).projcode
        assert row['sponsor'] is None

    def test_all_events_counts_zero_for_an_empty_event(self, session):
        event = make_account_request_event(session)
        row = next(r for r in all_events(session)
                   if r['event'].account_request_event_id == event.account_request_event_id)
        assert row['enrolled'] == 0

    def test_all_events_scopes_to_facilities_and_flags_an_inactive_project(self, session):
        event = make_account_request_event(session)
        project = session.get(Project, event.project_id)
        eid = event.account_request_event_id

        def _row(**kw):
            return next((r for r in all_events(session, **kw)
                         if r['event'].account_request_event_id == eid), None)

        row = _row()
        assert row['project_active'] is True and row['facility'] == project.facility_name
        assert _row(facility_names=['NO-SUCH-FACILITY']) is None
        assert _row(facility_names=[]) is None
        if project.facility_name:
            assert _row(facility_names=[project.facility_name]) is not None
        project.active = False
        session.flush()
        assert _row()['project_active'] is False

    def test_upcoming_skips_an_event_on_an_inactive_project(self, session):
        now = datetime(2026, 10, 1, 12, 0)
        event = make_account_request_event(session, listed=True,
                                           accounts_needed_by=now.date() + timedelta(days=5))
        session.get(Project, event.project_id).active = False
        session.flush()
        assert event.event_code not in {
            r['event_code'] for r in upcoming_listed_events(session, now=now)}

    def test_upcoming_is_listed_open_and_not_past_its_deadline(self, session):
        now = datetime(2026, 10, 1, 12, 0)
        soon = now.date() + timedelta(days=10)
        shown = make_account_request_event(session, listed=True, accounts_needed_by=soon)
        hidden = [
            make_account_request_event(session, accounts_needed_by=soon),  # unlisted
            make_account_request_event(session, listed=True, active=False,
                                       accounts_needed_by=soon),
            make_account_request_event(session, listed=True, accounts_needed_by=soon,
                                       opens_at=now + timedelta(days=1)),
            make_account_request_event(session, listed=True, accounts_needed_by=soon,
                                       closes_at=now),  # closes_at is exclusive
            make_account_request_event(session, listed=True,
                                       accounts_needed_by=now.date() - timedelta(days=1)),
        ]
        codes = {r['event_code'] for r in upcoming_listed_events(session, now=now)}
        assert shown.event_code in codes
        assert not codes & {e.event_code for e in hidden}

    def test_upcoming_rows_are_plain_picklable_dicts(self, session):
        import pickle
        now = datetime(2026, 10, 1, 12, 0)
        make_account_request_event(session, listed=True,
                                   accounts_needed_by=now.date() + timedelta(days=5))
        rows = upcoming_listed_events(session, now=now)
        assert rows and pickle.loads(pickle.dumps(rows)) == rows
        assert set(rows[0]) == {'event_id', 'event_code', 'name', 'instructions',
                                'project_code', 'accounts_needed_by', 'closes_at'}

    def test_enrolled_event_ids(self, session):
        user = make_user(session)
        event = make_account_request_event(session)
        assert enrolled_event_ids(session, user.user_id) == set()
        make_event_enrollment(session, event=event, user=user)
        assert enrolled_event_ids(session, user.user_id) == {event.account_request_event_id}
