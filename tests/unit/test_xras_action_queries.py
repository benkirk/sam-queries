"""Unit tests for ``sam.queries.xras_actions``.

These run against the raw test session, so rows are built directly and rolled back
by the per-test SAVEPOINT — no committed-row dance needed (that constraint belongs
to the HTTP tier, whose handlers read through Flask-SQLAlchemy's own connection).
"""

from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_project

from sam.core.users import EmailAddress
from sam.integration.xras import (
    XRAS_ACTIVATION_EVENT_TYPES,
    XrasActionLog,
    XrasActivationEvent,
)
from sam.queries.xras_actions import (
    XRAS_ACTION_SORT_COLUMNS,
    XRAS_ACTION_STATUSES,
    XRAS_REQUEST_TOKEN_EXAMPLE,
    XRAS_REQUEST_TOKEN_PREFIXES,
    canonical_action_type,
    count_recent_xras_actions,
    get_recent_xras_actions,
    summarize_xras_actions,
)
from sam.queries.xras_activation import (
    count_xras_dismissed_pending,
    get_latest_xras_action_id,
    get_xras_activation_events,
    get_xras_pending_activation,
    get_xras_pending_recipients,
)


def _action(session, *, status='received', action_type='Extension',
            request_number='UCUB0166', http_status=200, errors=None,
            received_time=None, replay_of_id=None, projcode_result=None,
            processed_by=None, payload='{"actionType":"Extension"}'):
    row = XrasActionLog(
        received_time=received_time or datetime.now(),
        remote_actor='samuel',
        action_type=action_type,
        request_number=request_number,
        raw_payload=payload,
        status=status,
        http_status=http_status,
        error_messages='\n'.join(errors) if errors else None,
        projcode_result=projcode_result,
        processed_by=processed_by,
        replay_of_id=replay_of_id,
    )
    session.add(row)
    session.flush()
    return row


def _event(session, project, event_type, *, when=None, by='benkirk',
           comment=None, notified_to=None):
    """Append an activation event, overriding creation_time where a test needs
    to place it relative to an action. ``create()`` always stamps *now*, which is
    right for production and useless for testing an ordering rule."""
    event = XrasActivationEvent.create(
        session, project_id=project.project_id, event_type=event_type,
        created_by=by, comment=comment, notified_to=notified_to,
    )
    if when is not None:
        event.creation_time = when
        session.flush()
    return event


def _email(session, user, address, *, is_primary=True):
    """Attach an address — ``make_user`` builds none, so a factory lead has no
    ``primary_email`` until a test says so.

    ``User.email_addresses`` is ``lazy='selectin'``, so a user already in the
    identity map carries an eagerly-loaded (empty) collection that adding a row
    does not invalidate. Expire it, or the address is invisible to anything that
    reaches the user through a relationship.
    """
    row = EmailAddress(email_address=address, user_id=user.user_id,
                       is_primary=is_primary, active=True)
    session.add(row)
    session.flush()
    session.expire(user, ['email_addresses'])
    return row


def _pending_row(session, project, **kwargs):
    """The one pending row for *project*, or None if it is hidden."""
    rows = [p for p in get_xras_pending_activation(session, **kwargs)
            if p['projcode'] == project.projcode]
    return rows[0] if rows else None


class TestErrorSplitting:
    def test_errors_come_back_as_an_ordered_list(self, session):
        row = _action(session, status='failed', http_status=422,
                      errors=['first: bad', 'second: worse', 'third: worst'])
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        # Order IS the contract — legacy accumulates into an ordered LinkedHashSet
        # so an operator can fix a request in one pass instead of five.
        assert got[0]['errors'] == ['first: bad', 'second: worse', 'third: worst']

    def test_no_errors_is_an_empty_list_not_none(self, session):
        row = _action(session)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['errors'] == []

    def test_blank_lines_are_dropped(self, session):
        row = _action(session, status='failed', errors=['a: x', '', 'b: y'])
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['errors'] == ['a: x', 'b: y']


class TestFilters:
    def test_status_accepts_a_scalar_or_a_list(self, session):
        _action(session, status='failed')
        _action(session, status='manual')
        base = count_recent_xras_actions(session)
        assert count_recent_xras_actions(session, status='failed') >= 1
        assert count_recent_xras_actions(
            session, status=['failed', 'manual']) <= base

    def test_an_empty_list_is_not_a_filter(self, session):
        """An empty multi-select must mean "no filter", not "match nothing" —
        htmx omits an unselected multi-select entirely."""
        _action(session)
        assert (count_recent_xras_actions(session, status=[])
                == count_recent_xras_actions(session))

    def test_http_status_separates_400_from_422(self, session):
        """The whole reason the column exists: both are status='failed'."""
        _action(session, status='failed', http_status=400, action_type=None)
        _action(session, status='failed', http_status=422)
        assert count_recent_xras_actions(session, http_status=400) >= 1
        assert count_recent_xras_actions(session, http_status=422) >= 1

    def test_has_errors_is_tri_state(self, session):
        _action(session, status='failed', errors=['x: y'])
        _action(session)
        with_errors = count_recent_xras_actions(session, has_errors=True)
        without = count_recent_xras_actions(session, has_errors=False)
        assert with_errors >= 1 and without >= 1
        assert with_errors + without == count_recent_xras_actions(session)

    def test_replays_only_is_tri_state(self, session):
        original = _action(session)
        _action(session, replay_of_id=original.xras_action_log_id,
                status='replayed')
        only = count_recent_xras_actions(session, replays_only=True)
        originals = count_recent_xras_actions(session, replays_only=False)
        assert only >= 1 and originals >= 1
        assert only + originals == count_recent_xras_actions(session)

    def test_replay_of_finds_the_children_of_one_row(self, session):
        parent = _action(session)
        child = _action(session, replay_of_id=parent.xras_action_log_id,
                        status='replayed')
        got = get_recent_xras_actions(session,
                                      replay_of=parent.xras_action_log_id)
        assert [r['action_log_id'] for r in got] == [child.xras_action_log_id]

    def test_date_bounds_are_inclusive(self, session):
        # microsecond=0 because `received_time` is a MySQL DATETIME with
        # second resolution: it truncates on write, so a bound carrying
        # microseconds would sort AFTER the row it was taken from and exclude
        # it. The app never hits this — the route's bounds are midnight and
        # 23:59:59 — but the test would be measuring MySQL, not the filter.
        when = (datetime.now() - timedelta(days=10)).replace(microsecond=0)
        row = _action(session, received_time=when)
        got = get_recent_xras_actions(
            session, action_log_id=row.xras_action_log_id,
            start_date=when, end_date=when)
        assert len(got) == 1

    def test_count_matches_the_row_query(self, session):
        _action(session, status='failed')
        _action(session, status='failed')
        rows = get_recent_xras_actions(session, status='failed')
        assert count_recent_xras_actions(session, status='failed') == len(rows)


class TestSorting:
    def test_unknown_sort_key_raises(self, session):
        """Defence in depth: the route whitelists too, but a raw column name
        must never reach order_by."""
        with pytest.raises(ValueError, match='Unknown sort_by'):
            get_recent_xras_actions(session, sort_by='raw_payload')

    def test_bad_sort_dir_raises(self, session):
        with pytest.raises(ValueError, match='sort_dir'):
            get_recent_xras_actions(session, sort_dir='sideways')

    def test_every_advertised_sort_key_works(self, session):
        _action(session)
        for key in XRAS_ACTION_SORT_COLUMNS:
            assert get_recent_xras_actions(session, sort_by=key, limit=1) \
                is not None

    def test_ties_on_received_time_break_on_the_primary_key(self, session):
        """Real seeding posts several payloads inside one second, so without the
        PK tiebreak paging is not stable."""
        when = datetime.now().replace(microsecond=0)   # see the note above
        ids = [_action(session, received_time=when).xras_action_log_id
               for _ in range(4)]
        got = get_recent_xras_actions(session, start_date=when, end_date=when)
        got_ids = [r['action_log_id'] for r in got if r['action_log_id'] in ids]
        assert got_ids == sorted(ids, reverse=True)


class TestPayloadGating:
    def test_payload_is_absent_by_default(self, session):
        """A list view has no business shipping ~3 KB of PII per row."""
        row = _action(session)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert 'raw_payload' not in got[0]

    def test_payload_is_present_when_asked_for(self, session):
        row = _action(session, payload='{"secret":"bytes"}')
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id,
                                      include_payload=True)
        assert got[0]['raw_payload'] == '{"secret":"bytes"}'


class TestReplayCount:
    def test_counts_children(self, session):
        parent = _action(session)
        for _ in range(3):
            _action(session, replay_of_id=parent.xras_action_log_id)
        got = get_recent_xras_actions(session,
                                      action_log_id=parent.xras_action_log_id)
        assert got[0]['replay_count'] == 3

    def test_is_zero_not_none_for_an_unreplayed_row(self, session):
        row = _action(session)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['replay_count'] == 0


class TestProjectExistenceFlags:
    """``request_number`` is a projcode for Extension/Supplement/Update and an
    ``NCAR####`` token for New. A UI linking every one of them would 404 on
    exactly the 21% of traffic with the worst failure rate."""

    def test_a_real_projcode_is_flagged(self, session, active_project):
        row = _action(session, request_number=active_project.projcode)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is True

    def test_an_ncar_request_token_is_not(self, session):
        row = _action(session, request_number='NCAR9999', action_type='New')
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False

    def test_result_projcode_is_flagged_independently(self, session,
                                                      active_project):
        row = _action(session, request_number='NCAR9999', action_type='New',
                      projcode_result=active_project.projcode)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False
        assert got[0]['result_is_project'] is True

    def test_null_codes_are_false_not_missing(self, session):
        row = _action(session, request_number=None, action_type=None)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False
        assert got[0]['result_is_project'] is False


class TestSummary:
    def test_every_status_appears_even_at_zero(self, session):
        """An absent bucket reads as "not measured" rather than "none"."""
        summary = summarize_xras_actions(session)
        assert set(summary['by_status']) == set(XRAS_ACTION_STATUSES)

    def test_total_equals_the_sum_of_buckets(self, session):
        _action(session, status='failed')
        _action(session, status='manual')
        summary = summarize_xras_actions(session)
        assert summary['total'] == sum(summary['by_status'].values())

    def test_by_type_carries_the_null_action_type_bucket(self, session):
        """A malformed body has no action type, and that IS the signal."""
        _action(session, status='failed', action_type=None, http_status=400)
        summary = summarize_xras_actions(session, status='failed')
        assert any(r['action_type'] is None for r in summary['by_type'])

    def test_summary_respects_filters(self, session):
        _action(session, status='failed')
        filtered = summarize_xras_actions(session, status='failed')
        assert filtered['by_status']['received'] == 0


class TestPendingActivation:
    def test_an_inactive_touched_project_is_listed(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode)
        pending = get_xras_pending_activation(session)
        assert project.projcode in {p['projcode'] for p in pending}

    def test_an_active_touched_project_is_not(self, session):
        project = make_project(session, active=True)
        _action(session, request_number=project.projcode)
        pending = get_xras_pending_activation(session)
        assert project.projcode not in {p['projcode'] for p in pending}

    def test_matches_on_projcode_result_too(self, session):
        """The New path: request_number is an NCAR token, the minted projcode
        lands in projcode_result."""
        project = make_project(session, active=False)
        _action(session, action_type='New', request_number='NCAR9999',
                projcode_result=project.projcode)
        pending = get_xras_pending_activation(session)
        assert project.projcode in {p['projcode'] for p in pending}

    def test_one_row_per_project_carrying_the_latest_action(self, session):
        project = make_project(session, active=False)
        older = datetime.now() - timedelta(days=5)
        _action(session, request_number=project.projcode, action_type='New',
                received_time=older)
        _action(session, request_number=project.projcode, action_type='Extension',
                received_time=datetime.now())
        rows = [p for p in get_xras_pending_activation(session)
                if p['projcode'] == project.projcode]
        assert len(rows) == 1
        assert rows[0]['action_type'] == 'Extension'

    def test_limit_is_honoured(self, session):
        for _ in range(3):
            project = make_project(session, active=False)
            _action(session, request_number=project.projcode)
        assert len(get_xras_pending_activation(session, limit=2)) == 2

    def test_a_project_with_no_events_reports_clean_derived_state(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode)
        row = _pending_row(session, project)
        assert row['dismissed'] is False
        assert row['notified_time'] is None
        assert row['notified_by'] is None
        assert row['notified_stale'] is False
        assert row['comment_count'] == 0


class TestActivationDeriveRule:
    """The whole reason there is no ``notified`` boolean.

    Each test here is one row of the behaviour table in
    ``docs/plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md`` § *The rule that does the real work*.
    A stored boolean gets the first three wrong.
    """

    def test_dismissing_hides_the_row(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'dismissed', comment='duplicate request')

        assert _pending_row(session, project) is None

    def test_a_dismissed_row_is_visible_when_asked_for(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'dismissed', comment='duplicate request')

        row = _pending_row(session, project, include_dismissed=True)
        assert row is not None
        assert row['dismissed'] is True
        assert row['dismissed_by'] == 'benkirk'
        assert row['dismissed_reason'] == 'duplicate request'

    def test_a_new_action_reopens_a_dismissed_project(self, session):
        """New information — the operator should look again. This is the case a
        boolean gets most wrong: it would stay hidden forever."""
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode, action_type='New',
                received_time=datetime.now() - timedelta(days=5))
        _event(session, project, 'dismissed',
               when=datetime.now() - timedelta(days=4))
        # An Extension arrives after the dismissal.
        _action(session, request_number=project.projcode, action_type='Extension',
                received_time=datetime.now())

        row = _pending_row(session, project)
        assert row is not None
        assert row['dismissed'] is False
        assert row['action_type'] == 'Extension'

    def test_restoring_reopens_a_dismissed_project(self, session):
        """Undo in an append-only table is a superseding event, not a DELETE."""
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=5))
        _event(session, project, 'dismissed',
               when=datetime.now() - timedelta(days=4))
        assert _pending_row(session, project) is None

        _event(session, project, 'restored', when=datetime.now())

        row = _pending_row(session, project)
        assert row is not None
        assert row['dismissed'] is False

    def test_a_dismissal_after_a_restore_hides_it_again(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=5))
        _event(session, project, 'dismissed',
               when=datetime.now() - timedelta(days=4))
        _event(session, project, 'restored',
               when=datetime.now() - timedelta(days=3))
        _event(session, project, 'dismissed',
               when=datetime.now() - timedelta(days=2))

        assert _pending_row(session, project) is None

    def test_notifying_with_nothing_new_is_not_stale(self, session):
        """Nobody is mailed twice about the same thing."""
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'notified', notified_to='Ben <benkirk@ucar.edu>')

        row = _pending_row(session, project)
        assert row['notified_time'] is not None
        assert row['notified_by'] == 'benkirk'
        assert row['notified_stale'] is False

    def test_a_new_action_makes_a_notification_stale(self, session):
        """The situation changed; telling them again is appropriate."""
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode, action_type='New',
                received_time=datetime.now() - timedelta(days=5))
        _event(session, project, 'notified',
               when=datetime.now() - timedelta(days=4))
        _action(session, request_number=project.projcode, action_type='Extension',
                received_time=datetime.now())

        row = _pending_row(session, project)
        assert row['notified_time'] is not None
        assert row['notified_stale'] is True

    def test_the_latest_notification_wins(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=5))
        _event(session, project, 'notified', by='someone',
               when=datetime.now() - timedelta(days=3))
        _event(session, project, 'notified', by='benkirk',
               when=datetime.now() - timedelta(days=1))

        row = _pending_row(session, project)
        assert row['notified_by'] == 'benkirk'

    def test_comment_count_counts_every_comment(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        for text in ('first', 'second', 'third'):
            _event(session, project, 'comment', comment=text)
        # Non-comment events must not inflate it.
        _event(session, project, 'notified')

        assert _pending_row(session, project)['comment_count'] == 3

    def test_activated_events_do_not_hide_a_still_inactive_project(self, session):
        """An 'activated' row whose effect did not land must not make the card
        lie. Only 'dismissed' hides."""
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'activated')

        assert _pending_row(session, project) is not None


class TestDismissedCount:
    def test_counts_only_hidden_rows(self, session):
        hidden = make_project(session, active=False)
        _action(session, request_number=hidden.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, hidden, 'dismissed')

        visible = make_project(session, active=False)
        _action(session, request_number=visible.projcode)

        before = count_xras_dismissed_pending(session)
        assert before >= 1

        # Restoring removes it from the hidden count.
        _event(session, hidden, 'restored')
        assert count_xras_dismissed_pending(session) == before - 1


class TestActivationEventModel:
    def test_create_rejects_an_unknown_event_type(self, session):
        project = make_project(session, active=False)
        with pytest.raises(ValueError, match='unknown'):
            XrasActivationEvent.create(
                session, project_id=project.project_id,
                event_type='notifed',        # the typo this guard exists for
                created_by='benkirk')

    def test_create_stamps_creation_time_from_the_app_clock(self, session):
        project = make_project(session, active=False)
        before = datetime.now()
        event = XrasActivationEvent.create(
            session, project_id=project.project_id, event_type='comment',
            created_by='benkirk', comment='hello')
        assert before <= event.creation_time <= datetime.now()

    def test_created_by_is_truncated_to_the_username_width(self, session):
        project = make_project(session, active=False)
        event = XrasActivationEvent.create(
            session, project_id=project.project_id, event_type='comment',
            created_by='x' * 80, comment='hello')
        assert len(event.created_by) == 35

    def test_the_restored_type_is_in_the_vocabulary(self, session):
        # The undo mechanism is a fifth value, added because the DDL carries no
        # ENUM/CHECK and therefore no second DBA ticket.
        assert 'restored' in XRAS_ACTIVATION_EVENT_TYPES


class TestActivationTimeline:
    def test_events_come_back_newest_first(self, session):
        project = make_project(session, active=False)
        _event(session, project, 'comment', comment='oldest',
               when=datetime.now() - timedelta(days=3))
        _event(session, project, 'notified',
               when=datetime.now() - timedelta(days=2))
        _event(session, project, 'comment', comment='newest',
               when=datetime.now() - timedelta(days=1))

        events = get_xras_activation_events(session, project.project_id)
        assert [e['event_type'] for e in events] == ['comment', 'notified', 'comment']
        assert events[0]['comment'] == 'newest'

    def test_a_project_with_no_events_is_an_empty_list(self, session):
        project = make_project(session, active=False)
        assert get_xras_activation_events(session, project.project_id) == []


class TestActionProvenance:
    def test_resolves_the_latest_action_naming_the_project(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=5))
        newest = _action(session, request_number=project.projcode,
                         received_time=datetime.now())

        got = get_latest_xras_action_id(session, project.project_id)
        assert got == newest.xras_action_log_id

    def test_matches_projcode_result_as_well_as_request_number(self, session):
        project = make_project(session, active=False)
        row = _action(session, action_type='New', request_number='NCAR9999',
                      projcode_result=project.projcode)
        assert get_latest_xras_action_id(
            session, project.project_id) == row.xras_action_log_id

    def test_an_untouched_project_has_no_provenance(self, session):
        project = make_project(session, active=False)
        assert get_latest_xras_action_id(session, project.project_id) is None

    def test_a_missing_project_is_none_not_an_error(self, session):
        assert get_latest_xras_action_id(session, 999_999_999) is None

    def test_a_same_second_tie_breaks_on_id_across_both_columns(self, session):
        """Two rows, one matching per column, same ``received_time``: id decides.

        ``received_time`` is a MySQL ``DATETIME`` — one-second resolution — and XRAS
        posts arrive in bursts, so a tie is not exotic. The rule has to be *some*
        deterministic thing, and "the row inserted last" is the only one available
        that an operator would also pick by eye.

        The alternative that was in place — first column processed wins — is not a
        rule anyone chose; it was a property of iterating
        ``(projcode_result, request_number)`` in that order.
        """
        project = make_project(session, active=False)
        when = datetime.now().replace(microsecond=0)

        _action(session, action_type='New', request_number='NCAR9999',
                projcode_result=project.projcode, received_time=when)
        later = _action(session, action_type='Extension',
                        request_number=project.projcode, received_time=when)

        assert get_latest_xras_action_id(
            session, project.project_id) == later.xras_action_log_id

    def test_the_pending_card_and_the_provenance_id_agree_on_a_tie(self, session):
        """The two must never name different rows — the card shows one action as the
        reason a project is pending, and ``xras_activation_event`` stamps the other
        as the provenance of what the operator then did about it.

        These were two spellings of one join with two tie-break rules: the provenance
        query ordered by ``(received_time DESC, id DESC)``, while the card merged two
        per-column queries comparing **only** ``received_time``, so on a tie whichever
        column was iterated first won regardless of id.
        """
        project = make_project(session, active=False)
        when = datetime.now().replace(microsecond=0)

        _action(session, action_type='New', request_number='NCAR9999',
                projcode_result=project.projcode, received_time=when)
        later = _action(session, action_type='Extension',
                        request_number=project.projcode, received_time=when)

        row = _pending_row(session, project)
        assert row is not None
        assert row['action_log_id'] == later.xras_action_log_id
        assert row['action_log_id'] == get_latest_xras_action_id(
            session, project.project_id)


class TestNotifyRecipients:
    def test_lead_is_returned_with_an_address(self, session):
        project = make_project(session, active=False)
        _email(session, project.lead, 'lead@example.edu')

        people = get_xras_pending_recipients(
            session, [project.project_id])[project.project_id]
        assert [p['role'] for p in people] == ['lead']
        assert people[0]['email'] == 'lead@example.edu'

    def test_lead_and_admin_are_both_returned(self, session):
        project = make_project(session, active=False)
        _email(session, project.lead, 'lead@example.edu')
        admin = make_user(session)
        _email(session, admin, 'admin@example.edu')
        project.project_admin_user_id = admin.user_id
        session.flush()

        people = get_xras_pending_recipients(
            session, [project.project_id])[project.project_id]
        assert [p['role'] for p in people] == ['lead', 'admin']

    def test_a_lead_who_is_also_admin_is_not_listed_twice(self, session):
        project = make_project(session, active=False)
        _email(session, project.lead, 'lead@example.edu')
        project.project_admin_user_id = project.project_lead_user_id
        session.flush()

        people = get_xras_pending_recipients(
            session, [project.project_id])[project.project_id]
        assert len(people) == 1

    def test_a_lead_with_no_address_on_file_is_an_empty_list(self, session):
        """Not an error, and not a blocker: the operator may have reached them
        out of band. The card renders a warning instead of hiding the button."""
        project = make_project(session, active=False)
        got = get_xras_pending_recipients(session, [project.project_id])
        assert got[project.project_id] == []

    def test_no_project_ids_is_an_empty_dict_not_a_full_scan(self, session):
        assert get_xras_pending_recipients(session, []) == {}


class TestActionTypeRollup:
    """``by_action_type`` — the marginal the facet chips need."""

    def test_counts_by_action_type(self, session):
        _action(session, action_type='Extension')
        _action(session, action_type='Extension')
        _action(session, action_type='New')
        summary = summarize_xras_actions(session)
        assert summary['by_action_type']['Extension'] >= 2
        assert summary['by_action_type']['New'] >= 1

    def test_null_action_type_keeps_its_bucket(self, session):
        """A body that would not parse has no action type. Dropping it here would
        stop by_action_type reconciling with total — callers rendering it as a
        *filter* skip None themselves, because IS NULL is not expressible."""
        _action(session, action_type=None, status='failed', http_status=400)
        summary = summarize_xras_actions(session)
        assert None in summary['by_action_type']
        assert sum(summary['by_action_type'].values()) == summary['total']

    def test_reconciles_with_by_status(self, session):
        _action(session, status='failed', action_type='New')
        _action(session, status='manual', action_type='Extension')
        summary = summarize_xras_actions(session)
        assert (sum(summary['by_action_type'].values())
                == sum(summary['by_status'].values())
                == summary['total'])

    def test_request_number_narrows_the_rollup(self, session):
        """The one table filter the summary used to ignore."""
        _action(session, request_number='UCUB0166')
        _action(session, request_number='UFSU0023')
        scoped = summarize_xras_actions(session, request_number='UCUB0166')
        assert scoped['total'] >= 1
        assert scoped['total'] < summarize_xras_actions(session)['total']


class TestActionTypeAliases:
    """``Adjust`` and ``Adjustment`` are one action, so they are one chip and one filter.

    XRAS sends ``Adjustment``; legacy's ``AdjustProjectActionService`` compares against
    ``Adjust`` and therefore never fires. Nothing has shipped here, so SAM accepts both
    spellings rather than reproducing the mismatch. The stored column keeps whatever
    arrived — folding is strictly read-side.
    """

    def test_canonical_and_alias_fold_onto_one_value(self):
        assert canonical_action_type('Adjust') == 'Adjustment'
        assert canonical_action_type('Adjustment') == 'Adjustment'
        # Unknown types and None pass through untouched — the vocabulary is open.
        assert canonical_action_type('Transfer') == 'Transfer'
        assert canonical_action_type(None) is None

    @pytest.mark.parametrize('asked', ['Adjustment', 'Adjust'])
    def test_either_spelling_selects_both_rows(self, session, asked):
        """Symmetric: asking by either spelling returns rows stored under both."""
        _action(session, action_type='Adjustment', request_number='UWIS0064')
        _action(session, action_type='Adjust', request_number='UWIS0065')

        rows = get_recent_xras_actions(session, action_type=asked)
        found = {r['request_number'] for r in rows}
        assert {'UWIS0064', 'UWIS0065'} <= found
        assert count_recent_xras_actions(session, action_type=asked) == len(rows)

    def test_a_non_aliased_type_is_unaffected(self, session):
        """The widening must not leak across types."""
        _action(session, action_type='Adjustment', request_number='UWIS0064')
        _action(session, action_type='New', request_number='NCAR4253')
        rows = get_recent_xras_actions(session, action_type='New')
        assert {r['request_number'] for r in rows} == {'NCAR4253'}

    def test_rollup_merges_the_two_spellings_into_one_bucket(self, session):
        """Two chips that filter identically would read as two distinct action types."""
        _action(session, action_type='Adjustment', request_number='UWIS0064')
        _action(session, action_type='Adjust', request_number='UWIS0065')

        summary = summarize_xras_actions(session)
        assert summary['by_action_type']['Adjustment'] >= 2
        assert 'Adjust' not in summary['by_action_type']
        # ...and the merge must not double-count or lose a row.
        assert sum(summary['by_action_type'].values()) == summary['total']

    def test_by_type_pairs_merge_too(self, session):
        """``by_type`` is the (status, action_type) cross product — one row per pair."""
        _action(session, action_type='Adjustment', status='manual',
                request_number='UWIS0064')
        _action(session, action_type='Adjust', status='manual',
                request_number='UWIS0065')

        summary = summarize_xras_actions(session)
        pairs = [r for r in summary['by_type']
                 if r['action_type'] == 'Adjustment' and r['status'] == 'manual']
        assert len(pairs) == 1
        assert pairs[0]['count'] >= 2
        assert not [r for r in summary['by_type'] if r['action_type'] == 'Adjust']
        assert sum(r['count'] for r in summary['by_type']) == summary['total']

    def test_the_stored_column_is_never_rewritten(self, session):
        """Audit fidelity: the row records the spelling XRAS actually sent."""
        _action(session, action_type='Adjust', request_number='UWIS0065')
        row = next(r for r in get_recent_xras_actions(session)
                   if r['request_number'] == 'UWIS0065')
        assert row['action_type'] == 'Adjust'


class TestFacetSelfExclusion:
    """The property that makes the chips switchers rather than dead ends.

    A dimension's rollup must omit its OWN filter. Scope it by itself and every
    unselected value reads zero the moment one is picked, so there is no way to
    move between values without first clearing the filter.
    """

    def test_omitting_status_keeps_other_statuses_visible(self, session):
        _action(session, status='failed', action_type='New')
        _action(session, status='manual', action_type='New')

        # How the route builds the STATUS facet: action_type applied, status not.
        facet = summarize_xras_actions(session, action_type='New')
        assert facet['by_status']['failed'] >= 1
        assert facet['by_status']['manual'] >= 1

    def test_scoping_a_dimension_by_itself_is_the_dead_end(self, session):
        """Pins the failure mode, so a refactor that reintroduces it is caught."""
        _action(session, status='failed')
        _action(session, status='manual')
        self_scoped = summarize_xras_actions(session, status='failed')
        assert self_scoped['by_status']['failed'] >= 1
        assert self_scoped['by_status']['manual'] == 0     # <- the dead end

    def test_omitting_action_type_keeps_other_types_visible(self, session):
        _action(session, status='failed', action_type='New')
        _action(session, status='failed', action_type='Extension')

        # How the route builds the ACTION TYPE facet: status applied, type not.
        facet = summarize_xras_actions(session, status='failed')
        assert facet['by_action_type'].get('New', 0) >= 1
        assert facet['by_action_type'].get('Extension', 0) >= 1


class TestRequestTokenDiscriminator:
    """The projcode discriminator must ask the DATABASE, never the string.

    This is the portability guard. `request_number` is a projcode for
    Extension/Supplement/Update and a site request token for New — and the two are
    the SAME SHAPE. Measured against real data, projcodes are `AAAA####`
    (`UCUB0166`, `UBOI0007`, `NACD0009`); `NCAR4232` is also eight characters in
    that shape. So no prefix or shape rule can separate them, and "simplifying"
    `_annotate_project_existence` into a `startswith` would be a portability bug
    AND wrong at NCAR.
    """

    def test_a_projcode_shaped_like_a_request_token_is_still_a_project(self, session):
        """The one that fails against any prefix-matching implementation.

        A project whose projcode legitimately begins with the token prefix must
        still resolve as a project. A `startswith(XRAS_REQUEST_TOKEN_PREFIXES)`
        rule would call this a request token and refuse to link it.
        """
        prefix = XRAS_REQUEST_TOKEN_PREFIXES[0]
        projcode = f'{prefix}0001'
        make_project(session, projcode=projcode)
        row = _action(session, request_number=projcode, action_type='New')

        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is True
        # It looks like a token AND is a real project — both flags are true, and
        # the *_is_project one is the one that decides the link.
        assert got[0]['request_is_token'] is True

    def test_a_token_with_no_project_is_not_a_project(self, session):
        row = _action(session, request_number=f'{XRAS_REQUEST_TOKEN_PREFIXES[0]}999999',
                      action_type='New')
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False
        assert got[0]['request_is_token'] is True

    def test_a_non_token_with_no_project_is_flagged_as_neither(self, session):
        """The state worth an operator's attention: an Extension naming a projcode
        SAM does not have — deleted, renamed, or a mis-sent payload."""
        row = _action(session, request_number='ZZZZ9999', action_type='Extension')
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False
        assert got[0]['request_is_token'] is False

    def test_a_real_projcode_is_a_project_and_not_a_token(self, session, active_project):
        row = _action(session, request_number=active_project.projcode,
                      action_type='Extension')
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is True
        assert got[0]['request_is_token'] is False

    def test_a_null_request_number_is_neither(self, session):
        row = _action(session, request_number=None, action_type=None)
        got = get_recent_xras_actions(session, action_log_id=row.xras_action_log_id)
        assert got[0]['request_is_project'] is False
        assert got[0]['request_is_token'] is False

    def test_the_token_family_is_a_tuple_so_a_site_can_list_several(self):
        """`startswith` takes a tuple — a site with more than one token form
        re-points this one name and nothing else."""
        assert isinstance(XRAS_REQUEST_TOKEN_PREFIXES, tuple)
        assert XRAS_REQUEST_TOKEN_PREFIXES, 'at least one prefix'
        assert XRAS_REQUEST_TOKEN_EXAMPLE.startswith(XRAS_REQUEST_TOKEN_PREFIXES), (
            'the example must be a member of the family it illustrates')


class TestAgesAreDeltasNotTimestamps:
    """``fmt_ago`` takes an elapsed ``timedelta``; handing it a ``datetime``
    raises ``AttributeError: 'datetime.datetime' object has no attribute
    'total_seconds'`` at render time.

    The unit tier could not catch that on its own — the pending-fragment render
    test runs against a snapshot with no activation events, so the badge that
    calls the filter never drew. These pin the shape the templates rely on.
    """

    def test_notified_age_is_a_timedelta(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'notified', when=datetime.now() - timedelta(hours=3))

        row = _pending_row(session, project)
        assert isinstance(row['notified_age'], timedelta)
        assert 2 < row['notified_age'].total_seconds() / 3600 < 4

    def test_a_just_written_event_can_read_slightly_negative(self, session):
        """Not a bug, and worth pinning so nobody "fixes" it into a crash.

        ``creation_time`` is stamped from the app clock with microseconds, and
        MySQL DATETIME **rounds** fractional seconds rather than truncating — so
        a row written at 10:10:24.894 is stored as 10:10:25, which is *ahead* of
        a ``datetime.now()`` taken moments later. The age is therefore allowed to
        be a small negative delta. ``fmt.ago`` clamps at zero
        (``max(delta.total_seconds(), 0)``) and renders "less than a minute".
        """
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode,
                received_time=datetime.now() - timedelta(days=2))
        _event(session, project, 'notified')

        age = _pending_row(session, project)['notified_age']
        assert isinstance(age, timedelta)
        assert age.total_seconds() > -1.0, 'more than rounding — a real inversion'

    def test_notified_age_is_none_when_never_notified(self, session):
        project = make_project(session, active=False)
        _action(session, request_number=project.projcode)
        assert _pending_row(session, project)['notified_age'] is None

    def test_every_timeline_event_carries_an_age_delta(self, session):
        project = make_project(session, active=False)
        _event(session, project, 'comment', comment='note')
        _event(session, project, 'notified')

        events = get_xras_activation_events(session, project.project_id)
        assert events
        assert all(isinstance(e['age'], timedelta) for e in events)
