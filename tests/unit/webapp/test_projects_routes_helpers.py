"""Tests for webapp admin projects_routes pure helpers.

The helpers under test (``_snap_to_end_of_month``, ``_propose_renew_dates``,
``_propose_extend_end``) compute default form values from source-allocation
date ranges. They take plain attribute-only objects, so tests use a trivial
stub and avoid the DB.
"""
from datetime import datetime


from webapp.dashboards.admin import projects_routes
from webapp.dashboards.admin.projects_routes import (
    _add_allocation_context,
    _build_alloc_candidates,
    _proposal_sources,
    _propose_extend_end,
    _propose_renew_dates,
    _resources_with_allocation,
    _snap_to_end_of_month,
)

from factories import make_account, make_allocation, make_project, make_resource




class _Alloc:
    """Minimal stand-in for Allocation — start_date / end_date only."""
    def __init__(self, start, end):
        self.start_date = start
        self.end_date = end


# ---------------------------------------------------------------------------
# _snap_to_end_of_month
# ---------------------------------------------------------------------------


class TestSnapToEndOfMonth:

    def test_mid_month_snaps_forward(self):
        assert _snap_to_end_of_month(datetime(2026, 4, 15)) == datetime(2026, 4, 30)

    def test_already_end_of_month_no_change(self):
        assert _snap_to_end_of_month(datetime(2026, 4, 30)) == datetime(2026, 4, 30)
        assert _snap_to_end_of_month(datetime(2026, 12, 31)) == datetime(2026, 12, 31)

    def test_day_one_snaps_backward(self):
        # May 1 -> Apr 30 (previous month's last day)
        assert _snap_to_end_of_month(datetime(2026, 5, 1)) == datetime(2026, 4, 30)

    def test_jan_one_crosses_year_boundary(self):
        # Jan 1 2028 -> Dec 31 2027
        assert _snap_to_end_of_month(datetime(2028, 1, 1)) == datetime(2027, 12, 31)

    def test_late_month_snaps_forward(self):
        # Oct 29 -> Oct 31 (the observed CESM0002-style drift)
        assert _snap_to_end_of_month(datetime(2027, 10, 29)) == datetime(2027, 10, 31)

    def test_leap_february(self):
        assert _snap_to_end_of_month(datetime(2024, 2, 3)) == datetime(2024, 2, 29)

    def test_non_leap_february(self):
        assert _snap_to_end_of_month(datetime(2025, 2, 3)) == datetime(2025, 2, 28)

    def test_preserves_time_of_day(self):
        result = _snap_to_end_of_month(datetime(2026, 4, 15, 10, 30, 45))
        assert result == datetime(2026, 4, 30, 10, 30, 45)


# ---------------------------------------------------------------------------
# _propose_renew_dates
# ---------------------------------------------------------------------------


class TestProposeRenewDates:

    def test_fiscal_year_source_is_contiguous_and_aligned(self):
        # Nov 1 2024 -> Oct 31 2025 (1-year fiscal). Renew should propose
        # Nov 1 2025 -> Oct 31 2026.
        src = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        assert _propose_renew_dates([src]) == ('2025-11-01', '2026-10-31')

    def test_eighteen_month_source_snaps_forward(self):
        # CESM0002-style source: Nov 1 2024 -> Apr 30 2026 (~18 months).
        # new_start = May 1 2026; new_start + period lands on Oct 29-30 2027
        # and snaps to Oct 31. Regression guard for the user-reported drift.
        src = _Alloc(datetime(2024, 11, 1), datetime(2026, 4, 30))
        _, end = _propose_renew_dates([src])
        assert end == '2027-10-31'

    def test_two_year_jan_dec_source_snaps_backward(self):
        # 2-year Jan 1 -> Dec 31 source. new_start + period lands on Jan 1,
        # which must snap BACKWARD to Dec 31 of the prior year (not forward
        # to Jan 31 — that would miss by a month).
        src = _Alloc(datetime(2024, 1, 1), datetime(2025, 12, 31))
        assert _propose_renew_dates([src]) == ('2026-01-01', '2027-12-31')

    def test_multiple_sources_anchors_on_latest_end(self):
        # Two sources with different periods; anchor must be the latest-ending.
        earlier = _Alloc(datetime(2024, 1, 1), datetime(2025, 3, 31))
        latest  = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        start, end = _propose_renew_dates([earlier, latest])
        # Anchored on latest — Nov 1 next year start, snapped Oct 31 end
        assert start == '2025-11-01'
        assert end == '2026-10-31'

    def test_open_ended_sources_ignored_falls_back_to_today_plus_year(self):
        # Open-ended source (end_date=None) is skipped; fallback is used.
        src = _Alloc(datetime(2020, 1, 1), None)
        start, end = _propose_renew_dates([src])
        # End should be end-of-month one year from today — just assert shape.
        assert len(start) == 10 and start[4] == '-' and start[7] == '-'
        assert len(end) == 10
        # end must be a month-end (day 28-31)
        assert 28 <= int(end[8:10]) <= 31

    def test_empty_source_list_falls_back(self):
        start, end = _propose_renew_dates([])
        assert len(start) == 10
        assert 28 <= int(end[8:10]) <= 31


# ---------------------------------------------------------------------------
# _propose_extend_end
# ---------------------------------------------------------------------------


class TestProposeExtendEnd:

    def test_fiscal_year_source_extends_one_year(self):
        # Source period = 1 year; extend adds another year.
        src = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        assert _propose_extend_end([src]) == '2026-10-31'

    def test_two_year_jan_dec_source_snaps_backward(self):
        # 2-year source: extending 2 years lands on Dec 31 (not Jan 31).
        src = _Alloc(datetime(2024, 1, 1), datetime(2025, 12, 31))
        assert _propose_extend_end([src]) == '2027-12-31'

    def test_six_month_source_extends_six_months(self):
        # Half-year source; end + 6mo ≈ Sep 30. Must snap to Sep 30.
        src = _Alloc(datetime(2025, 4, 1), datetime(2025, 9, 30))
        assert _propose_extend_end([src]) == '2026-03-31'

    def test_open_ended_source_ignored(self):
        # Only open-ended source -> no proposal.
        src = _Alloc(datetime(2020, 1, 1), None)
        assert _propose_extend_end([src]) == ''

    def test_no_sources(self):
        assert _propose_extend_end([]) == ''

    def test_multiple_sources_anchors_on_latest_end(self):
        earlier = _Alloc(datetime(2024, 1, 1), datetime(2025, 3, 31))
        latest  = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        # Anchor = latest; period = 1 year; end_date + period = Oct 31 2026.
        assert _propose_extend_end([earlier, latest]) == '2026-10-31'


# ---------------------------------------------------------------------------
# _resources_with_allocation — the "Add Allocation" dropdown exclusion set.
# Regression guard for the WYOM0253 bug: an empty (allocation-less) account
# must NOT hide its resource from the dropdown.
# ---------------------------------------------------------------------------


class TestResourcesWithAllocation:

    def test_empty_account_does_not_hide_its_resource(self, session):
        """A live account with zero allocations is excluded from the set, so
        its resource stays offerable (the WYOM0253 / Derecho case)."""
        project = make_project(session)
        empty_res = make_resource(session)
        make_account(session, project=project, resource=empty_res)  # no allocation

        assert empty_res.resource_id not in _resources_with_allocation(project)

    def test_resource_with_allocation_is_included(self, session):
        """A resource the project actually holds an allocation on is in the set
        (so it's filtered out of the dropdown)."""
        project = make_project(session)
        allocated_res = make_resource(session)
        acct = make_account(session, project=project, resource=allocated_res)
        make_allocation(session, account=acct)

        assert allocated_res.resource_id in _resources_with_allocation(project)

    def test_mixed_project_returns_only_allocated_resources(self, session):
        """Only resources with an allocation appear; empty-account resources don't."""
        project = make_project(session)
        allocated_res = make_resource(session)
        empty_res = make_resource(session)
        acct = make_account(session, project=project, resource=allocated_res)
        make_allocation(session, account=acct)
        make_account(session, project=project, resource=empty_res)

        result = _resources_with_allocation(project)
        assert allocated_res.resource_id in result
        assert empty_res.resource_id not in result

    def test_soft_deleted_account_with_allocation_is_excluded(self, session):
        """A soft-deleted account is excluded so its resource stays offerable;
        Account.get_or_create revives the row on re-allocation rather than
        colliding on project_resource_ux."""
        project = make_project(session)
        resource = make_resource(session)
        acct = make_account(session, project=project, resource=resource)
        make_allocation(session, account=acct)
        acct.deleted = True
        session.flush()

        assert resource.resource_id not in _resources_with_allocation(project)


# ---------------------------------------------------------------------------
# Sub-project-only resources in the Renew/Extend candidate list
# ---------------------------------------------------------------------------


class TestProposalSources:

    def test_root_sources_win_over_a_partial_child_only_period(self):
        # NTMA0002/Destor shape: a Feb -> Sep child-only source must not drag
        # the proposed period away from the root's fiscal year.
        root = _Alloc(datetime(2025, 10, 1), datetime(2026, 9, 30))
        child = _Alloc(datetime(2026, 2, 25), datetime(2026, 9, 30))
        candidates = [
            {'source_alloc': child, 'child_only': True},
            {'source_alloc': root, 'child_only': False},
        ]
        assert _proposal_sources(candidates) == [root]
        assert _propose_renew_dates(_proposal_sources(candidates)) == (
            '2026-10-01', '2027-09-30')

    def test_child_only_sources_used_when_the_root_has_none(self):
        child = _Alloc(datetime(2025, 10, 1), datetime(2026, 9, 30))
        assert _proposal_sources([{'source_alloc': child, 'child_only': True}]) == [child]


class TestBuildAllocCandidates:

    def test_lists_child_only_resource_with_its_anchor(self, app, session):
        root = make_project(session)
        child = make_project(session, parent=root)
        session.expire_all()
        root = session.get(type(root), root.project_id)
        root_res, child_res = make_resource(session), make_resource(session)
        make_allocation(session, account=make_account(session, project=root, resource=root_res),
                        start_date=datetime(2099, 1, 1), end_date=datetime(2099, 12, 31))
        make_allocation(session, account=make_account(session, project=child, resource=child_res),
                        amount=300.0,
                        start_date=datetime(2099, 1, 1), end_date=datetime(2099, 12, 31))
        session.expire_all()
        root = session.get(type(root), root.project_id)

        with app.app_context():
            rows = {c['resource_id']: c
                    for c in _build_alloc_candidates(root, datetime(2099, 6, 15))}
        assert set(rows) == {root_res.resource_id, child_res.resource_id}
        assert rows[root_res.resource_id]['child_only'] is False
        child_row = rows[child_res.resource_id]
        assert child_row['child_only'] is True
        assert child_row['anchor_projcodes'] == [child.projcode]
        assert child_row['amount'] == 300.0


# ---------------------------------------------------------------------------
# _add_allocation_context (the Add Allocations grid)
# ---------------------------------------------------------------------------


def _grid_rows(ctx):
    return {row['resource'].resource_id: row
            for g in ctx['resource_groups'] for row in g['rows']}


class TestAddAllocationContext:

    def test_common_resource_is_shown_specialty_is_optional(self, session, monkeypatch):
        project = make_project(session)
        common, special = make_resource(session), make_resource(session)
        monkeypatch.setattr(projects_routes, 'COMMON_ALLOCATION_RESOURCES',
                            (common.resource_name,))

        rows = _grid_rows(_add_allocation_context(project))

        assert rows[common.resource_id]['optional'] is False
        assert rows[special.resource_id]['optional'] is True
        assert not rows[special.resource_id]['held']

    def test_held_specialty_resource_is_always_shown(self, session, monkeypatch):
        project = make_project(session)
        special = make_resource(session)
        end = datetime(2027, 9, 30, 23, 59, 59)
        make_allocation(session, end_date=end,
                        account=make_account(session, project=project, resource=special))
        monkeypatch.setattr(projects_routes, 'COMMON_ALLOCATION_RESOURCES', ())

        row = _grid_rows(_add_allocation_context(project))[special.resource_id]

        assert row['held'] is True
        assert row['optional'] is False
        assert row['existing_end'] == end

    def test_group_is_optional_only_when_every_row_is(self, session, monkeypatch):
        project = make_project(session)
        common = make_resource(session)
        special = make_resource(session, resource_type=common.resource_type)
        lone = make_resource(session)
        monkeypatch.setattr(projects_routes, 'COMMON_ALLOCATION_RESOURCES',
                            (common.resource_name,))

        groups = {g['type_name']: g for g in
                  _add_allocation_context(project)['resource_groups']}

        assert groups[common.resource_type.resource_type]['optional'] is False
        assert groups[lone.resource_type.resource_type]['optional'] is True
        assert special.resource_id in {
            r['resource'].resource_id
            for r in groups[common.resource_type.resource_type]['rows']}

    def test_stale_common_list_shows_everything(self, session, monkeypatch):
        project = make_project(session)
        make_resource(session)
        monkeypatch.setattr(projects_routes, 'COMMON_ALLOCATION_RESOURCES',
                            ('no-such-resource',))

        assert _add_allocation_context(project)['show_all_default'] is True
