"""`sam.manage.allocations.align_project_allocations` — uniform period of performance."""

from datetime import datetime

import pytest
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.accounting.allocations import AllocationTransaction
from sam.manage.allocations import align_project_allocations, alignment_target


def _acct(session, project):
    return make_account(session, project=project, resource=make_resource(session))


def _dated(session, project, start, end, **kw):
    return make_allocation(session, account=_acct(session, project),
                           start_date=start, end_date=end, **kw)


class TestAlignmentTarget:

    def test_fewer_than_two_dated_is_none(self):
        class S:
            def __init__(s, a, b): s.start_date, s.end_date = a, b
        assert alignment_target([S(datetime(2027, 1, 1), datetime(2027, 9, 30))]) is None
        # one dated + one open-ended is still < 2 dated
        assert alignment_target([S(datetime(2027, 1, 1), None),
                                 S(datetime(2027, 1, 1), datetime(2027, 9, 30))]) is None

    def test_widest_window(self):
        class S:
            def __init__(s, a, b): s.start_date, s.end_date = a, b
        target = alignment_target([S(datetime(2027, 1, 1), datetime(2027, 9, 30)),
                                   S(datetime(2026, 10, 1), datetime(2027, 12, 31))])
        assert target == (datetime(2026, 10, 1), datetime(2027, 12, 31))


class TestAlign:

    def test_misaligned_resources_become_uniform(self, session):
        # Values chosen so BOTH differ from the target [min start, max end] =
        # [2026-10-01, 2027-09-30]: a supplies the max end, b the min start.
        proj = make_project(session)
        a = _dated(session, proj, datetime(2027, 1, 1), datetime(2027, 9, 30))
        b = _dated(session, proj, datetime(2026, 10, 1), datetime(2027, 6, 30))
        session.flush()
        changed = align_project_allocations(
            session, root_project_id=proj.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1)
        assert len(changed) == 2
        for alloc in (a, b):
            session.refresh(alloc)
            assert alloc.start_date == datetime(2026, 10, 1)
            assert alloc.end_date.date() == datetime(2027, 9, 30).date()

    def test_already_aligned_is_a_noop(self, session):
        proj = make_project(session)
        start, end = datetime(2026, 10, 1), datetime(2027, 9, 30)
        _dated(session, proj, start, end)
        _dated(session, proj, start, end)
        session.flush()
        assert align_project_allocations(
            session, root_project_id=proj.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1) == []

    def test_open_ended_resource_is_left_untouched(self, session):
        proj = make_project(session)
        _dated(session, proj, datetime(2027, 1, 1), datetime(2027, 9, 30))
        _dated(session, proj, datetime(2026, 10, 1), datetime(2027, 12, 31))
        open_acct = _acct(session, proj)
        open_alloc = make_allocation(session, account=open_acct, amount=1.0,
                                     start_date=datetime(2027, 1, 1))
        open_alloc.end_date = None
        session.flush()
        align_project_allocations(
            session, root_project_id=proj.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1)
        session.refresh(open_alloc)
        assert open_alloc.end_date is None
        assert open_alloc.start_date == datetime(2027, 1, 1)

    def test_single_dated_resource_returns_empty(self, session):
        proj = make_project(session)
        _dated(session, proj, datetime(2027, 1, 1), datetime(2027, 9, 30))
        session.flush()
        assert align_project_allocations(
            session, root_project_id=proj.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1) == []

    def test_cascade_to_inheriting_child(self, session):
        root = make_project(session)
        r1 = make_resource(session)
        root_acct = make_account(session, project=root, resource=r1)
        root_alloc = make_allocation(session, account=root_acct,
                                     start_date=datetime(2027, 1, 1),
                                     end_date=datetime(2027, 9, 30))
        # a second dated resource so there IS a wider target to align to
        _dated(session, root, datetime(2026, 10, 1), datetime(2027, 12, 31))
        child = make_project(session, parent=root)
        child_acct = make_account(session, project=child, resource=r1)
        child_alloc = make_allocation(session, account=child_acct, parent=root_alloc,
                                      start_date=datetime(2027, 1, 1),
                                      end_date=datetime(2027, 9, 30))
        session.flush()
        align_project_allocations(
            session, root_project_id=root.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1)
        session.refresh(child_alloc)
        assert child_alloc.start_date == datetime(2026, 10, 1)
        assert child_alloc.end_date.date() == datetime(2027, 12, 31).date()

    def test_amounts_unchanged_and_audit_row_is_zero_delta(self, session):
        proj = make_project(session)
        a = _dated(session, proj, datetime(2027, 1, 1), datetime(2027, 9, 30))
        _dated(session, proj, datetime(2026, 10, 1), datetime(2027, 12, 31))
        session.flush()
        before = a.amount
        align_project_allocations(
            session, root_project_id=proj.project_id,
            source_active_at=datetime(2027, 2, 1), user_id=1)
        session.refresh(a)
        assert a.amount == before
        rows = session.query(AllocationTransaction).filter(
            AllocationTransaction.allocation_id == a.allocation_id).all()
        # a date-only edit logs a row, and it carries no amount delta
        assert any(r.transaction_amount == 0 for r in rows)
