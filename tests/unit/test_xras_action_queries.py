"""Unit tests for ``sam.queries.xras_actions``.

These run against the raw test session, so rows are built directly and rolled back
by the per-test SAVEPOINT — no committed-row dance needed (that constraint belongs
to the HTTP tier, whose handlers read through Flask-SQLAlchemy's own connection).
"""

from datetime import datetime, timedelta

import pytest
from factories.projects import make_project

from sam.integration.xras import XrasActionLog
from sam.queries.xras_actions import (
    XRAS_ACTION_SORT_COLUMNS,
    XRAS_ACTION_STATUSES,
    count_recent_xras_actions,
    get_recent_xras_actions,
    get_xras_pending_activation,
    summarize_xras_actions,
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
