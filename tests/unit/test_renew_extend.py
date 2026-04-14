"""
Unit tests for sam.manage.renew and sam.manage.extend.

Covers tree-aware renewal and end-date extension across three project
topologies:
  - **Standalone** (SCSG0001-like): single project, no descendants.
  - **Inheriting tree** (NMMM0003-like): root + inheriting children
    (``parent_allocation_id`` chain).
  - **Divergent tree** (CESM0002-like): root + standalone sub-project
    allocations (each node has its own amount, no parent link).

Strategy: build fresh test allocations on *real* project trees at
far-future dates (2099+) so the test source rows never collide with
real production data. ``find_source_alloc_at`` prefers the latest
start_date, so our future allocations always win the "active at"
lookup. Session rollback cleans up.
"""

import pytest
from datetime import datetime, timedelta

from sam import Allocation, Project, Resource
from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
)
from sam.manage.renew import (
    renew_project_allocations,
    find_source_alloc_at,
    find_source_allocations_at,
    find_renewable_descendants,
)
from sam.manage.extend import extend_project_allocations


# ---------------------------------------------------------------------------
# Constants — far-future dates so tests never collide with real data
# ---------------------------------------------------------------------------

SRC_START = datetime(2099, 1, 1)
SRC_END = datetime(2099, 12, 31, 23, 59, 59)
SRC_ACTIVE_AT = datetime(2099, 6, 15)

NEW_START = datetime(2100, 1, 1)
NEW_END = datetime(2100, 12, 31, 23, 59, 59)

TEST_USER_ID = 1
ROOT_AMOUNT = 1_000_000.0
CHILD_BASE_AMOUNT = 100_000.0


# ---------------------------------------------------------------------------
# Helpers — use module-level so tests can build topology on demand
# ---------------------------------------------------------------------------

def _require_project(session, projcode):
    proj = Project.get_by_projcode(session, projcode)
    if proj is None:
        pytest.skip(f"{projcode} not found in test database")
    return proj


def _require_resource(session, name='Derecho'):
    res = session.query(Resource).filter_by(resource_name=name).first()
    if res is None:
        pytest.skip(f"Resource {name!r} not found in test database")
    return res


def _active_descendants(project):
    return [d for d in project.get_descendants() if d.active]


def _seed_standalone_source(session, project, resource, amount=ROOT_AMOUNT,
                            start=SRC_START, end=SRC_END):
    """Create one non-inheriting test allocation on ``project`` for ``resource``.

    Expires the session after flush so any previously-loaded ``project.accounts``
    relationship collection is re-queried on next access — otherwise tests that
    hit projects already loaded by ``get_descendants()`` see a stale cache.
    """
    alloc = Allocation.create(
        session,
        project_id=project.project_id,
        resource_id=resource.resource_id,
        amount=amount,
        start_date=start,
        end_date=end,
    )
    session.flush()
    session.expire_all()
    return alloc


def _seed_inheriting_tree(session, root, resource, amount=ROOT_AMOUNT):
    """Create a root + inheriting children tree on every active descendant.

    Mirrors the shared-pool topology: all nodes share the same amount and
    each child's ``parent_allocation_id`` points at its immediate
    project-parent's allocation. Returns (root_alloc, {project_id: alloc}).
    """
    root_alloc = _seed_standalone_source(session, root, resource, amount=amount)
    alloc_map = {root.project_id: root_alloc}
    for descendant in _active_descendants(root):
        parent_alloc = alloc_map.get(descendant.parent_id)
        child = Allocation.create(
            session,
            project_id=descendant.project_id,
            resource_id=resource.resource_id,
            amount=amount,
            start_date=SRC_START,
            end_date=SRC_END,
            parent_allocation_id=parent_alloc.allocation_id if parent_alloc else None,
        )
        alloc_map[descendant.project_id] = child
    session.flush()
    session.expire_all()
    return root_alloc, alloc_map


def _seed_divergent_tree(session, root, resource, amount=ROOT_AMOUNT):
    """Create a root + *standalone* sub-project allocations, each with its
    own distinct amount. Returns (root_alloc, {project_id: (alloc, amount)}).
    """
    root_alloc = _seed_standalone_source(session, root, resource, amount=amount)
    child_allocs = {}
    for i, descendant in enumerate(_active_descendants(root)):
        child_amount = CHILD_BASE_AMOUNT + (i * 10_000.0)
        child = Allocation.create(
            session,
            project_id=descendant.project_id,
            resource_id=resource.resource_id,
            amount=child_amount,
            start_date=SRC_START,
            end_date=SRC_END,
        )
        child_allocs[descendant.project_id] = (child, child_amount)
    session.flush()
    session.expire_all()
    return root_alloc, child_allocs


def _find_test_alloc(session, project, resource_id, active_at):
    """Return the test (2099-era) allocation on ``project`` for ``resource_id``.

    Does not rely on relationship caching — queries directly so it picks
    up brand-new rows even if the project was loaded earlier.
    """
    from sam.accounting.accounts import Account
    return (
        session.query(Allocation)
        .join(Account, Allocation.account_id == Account.account_id)
        .filter(
            Account.project_id == project.project_id,
            Account.resource_id == resource_id,
            Allocation.deleted == False,
            Allocation.start_date <= active_at,
        )
        .filter(
            (Allocation.end_date == None) | (Allocation.end_date >= active_at)
        )
        .order_by(Allocation.start_date.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# find_source_alloc_at
# ---------------------------------------------------------------------------

class TestFindSourceAllocAt:

    def test_returns_standalone_source(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        seeded = _seed_standalone_source(session, project, resource)

        found = find_source_alloc_at(project, resource.resource_id, SRC_ACTIVE_AT)

        assert found is not None
        assert found.allocation_id == seeded.allocation_id

    def test_returns_none_when_no_active_source(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        _seed_standalone_source(session, project, resource)

        # Check far outside the seeded window
        out_of_range = datetime(2098, 6, 15)
        found = find_source_alloc_at(
            project, resource.resource_id, out_of_range
        )
        assert found is None

    def test_returns_latest_start_when_multiple_active(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        # Earlier overlapping source
        _seed_standalone_source(
            session, project, resource,
            start=datetime(2099, 1, 1), end=datetime(2099, 12, 31, 23, 59, 59),
        )
        # Later overlapping source — should win
        winner = _seed_standalone_source(
            session, project, resource,
            start=datetime(2099, 4, 1), end=datetime(2099, 10, 31, 23, 59, 59),
        )

        found = find_source_alloc_at(
            project, resource.resource_id, datetime(2099, 6, 1),
        )
        assert found.allocation_id == winner.allocation_id


# ---------------------------------------------------------------------------
# find_source_allocations_at
# ---------------------------------------------------------------------------

class TestFindSourceAllocationsAt:

    def test_excludes_inheriting_allocations(self, session):
        root = _require_project(session, 'NMMM0003')
        if not _active_descendants(root):
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        results = find_source_allocations_at(session, root, SRC_ACTIVE_AT)

        # Exactly one root-level (non-inheriting) source for our resource
        for alloc in results:
            assert not alloc.is_inheriting


# ---------------------------------------------------------------------------
# find_renewable_descendants
# ---------------------------------------------------------------------------

class TestFindRenewableDescendants:

    def test_inheriting_tree_returns_all_active_descendants(self, session):
        root = _require_project(session, 'NMMM0003')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        found = find_renewable_descendants(
            root, resource.resource_id, SRC_ACTIVE_AT
        )
        found_ids = {d.project_id for d in found}
        assert {d.project_id for d in descendants} <= found_ids

    def test_divergent_tree_returns_standalone_children(self, session):
        root = _require_project(session, 'CESM0002')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("CESM0002 has no active descendants")
        resource = _require_resource(session)
        _seed_divergent_tree(session, root, resource)

        found = find_renewable_descendants(
            root, resource.resource_id, SRC_ACTIVE_AT
        )
        found_ids = {d.project_id for d in found}
        # Every seeded descendant is picked up (standalone allocations count)
        assert {d.project_id for d in descendants} <= found_ids


# ---------------------------------------------------------------------------
# renew_project_allocations
# ---------------------------------------------------------------------------

class TestRenewStandalone:

    def test_creates_new_root_allocation(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(session, project, resource)

        created = renew_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert len(created) == 1
        new_alloc = created[0]
        assert new_alloc.allocation_id != source.allocation_id
        assert new_alloc.amount == source.amount
        assert new_alloc.start_date == NEW_START
        assert new_alloc.end_date == NEW_END
        assert new_alloc.parent_allocation_id is None

    def test_logs_renew_transaction(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        _seed_standalone_source(session, project, resource)

        created = renew_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=created[0].allocation_id,
                transaction_type=AllocationTransactionType.RENEW,
            )
            .first()
        )
        assert txn is not None
        assert txn.propagated is False

    def test_invalid_dates_raise(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        _seed_standalone_source(session, project, resource)

        with pytest.raises(ValueError):
            renew_project_allocations(
                session,
                root_project_id=project.project_id,
                source_active_at=SRC_ACTIVE_AT,
                new_start=NEW_END,       # inverted
                new_end=NEW_START,
                resource_ids=[resource.resource_id],
                user_id=TEST_USER_ID,
            )


class TestRenewInheritingTree:

    def test_creates_new_root_plus_inheriting_children(self, session):
        root = _require_project(session, 'NMMM0003')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        created = renew_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert len(created) == 1
        new_root = created[0]
        assert new_root.parent_allocation_id is None

        # Every descendant has a renewed allocation at NEW_START/NEW_END
        for descendant in descendants:
            renewed = _find_test_alloc(
                session, descendant, resource.resource_id, NEW_START,
            )
            assert renewed is not None, f"{descendant.projcode} missing renewal"
            assert renewed.start_date == NEW_START
            assert renewed.end_date == NEW_END
            assert renewed.amount == ROOT_AMOUNT
            assert renewed.parent_allocation_id is not None

    def test_topology_wired_to_new_root(self, session):
        root = _require_project(session, 'NMMM0003')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        created = renew_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )
        new_root = created[0]

        # Walk the new tree — every renewed allocation's ultimate ancestor is new_root
        for descendant in descendants:
            renewed = _find_test_alloc(
                session, descendant, resource.resource_id, NEW_START,
            )
            ancestor = renewed
            seen = set()
            while ancestor.parent_allocation_id is not None:
                if ancestor.allocation_id in seen:
                    pytest.fail("parent chain cycles")
                seen.add(ancestor.allocation_id)
                ancestor = session.get(Allocation, ancestor.parent_allocation_id)
            assert ancestor.allocation_id == new_root.allocation_id


class TestRenewDivergentTree:

    def test_preserves_per_node_amounts(self, session):
        root = _require_project(session, 'CESM0002')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("CESM0002 has no active descendants")
        resource = _require_resource(session)
        _, child_allocs = _seed_divergent_tree(session, root, resource)

        renew_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        for descendant in descendants:
            _, expected_amount = child_allocs[descendant.project_id]
            renewed = _find_test_alloc(
                session, descendant, resource.resource_id, NEW_START,
            )
            assert renewed is not None, f"{descendant.projcode} missing renewal"
            assert renewed.amount == expected_amount
            assert renewed.parent_allocation_id is None   # stays standalone


class TestRenewResourceSelection:

    def test_only_requested_resource_renewed(self, session):
        project = _require_project(session, 'SCSG0001')
        derecho = _require_resource(session, 'Derecho')
        casper = _require_resource(session, 'Casper')
        _seed_standalone_source(session, project, derecho)
        _seed_standalone_source(session, project, casper)

        created = renew_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=TEST_USER_ID,
        )

        assert len(created) == 1
        assert created[0].account.resource_id == derecho.resource_id

        # Casper not renewed
        casper_renewed = _find_test_alloc(
            session, project, casper.resource_id, NEW_START,
        )
        assert casper_renewed is None


class TestRenewIdempotency:

    def test_second_call_is_noop(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        _seed_standalone_source(session, project, resource)

        first = renew_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )
        # Simulate a fresh request: production sessions don't hold stale
        # relationship caches across operations.
        session.expire_all()
        second = renew_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert len(first) == 1
        assert len(second) == 0   # overlapping target blocks re-creation


# ---------------------------------------------------------------------------
# extend_project_allocations
# ---------------------------------------------------------------------------

EXTENDED_END = datetime(2100, 6, 30, 23, 59, 59)


class TestExtendStandalone:

    def test_pushes_root_end_date(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(session, project, resource)

        updated = extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert len(updated) == 1
        assert updated[0].allocation_id == source.allocation_id
        assert source.end_date == EXTENDED_END

    def test_logs_extension_transaction(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(session, project, resource)

        extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=source.allocation_id,
                transaction_type=AllocationTransactionType.EXTENSION,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is False


class TestExtendInheritingTree:

    def test_pushes_root_and_all_children(self, session):
        root = _require_project(session, 'NMMM0003')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        extend_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        # Root + all descendants now end at EXTENDED_END
        root_alloc = _find_test_alloc(
            session, root, resource.resource_id, SRC_ACTIVE_AT,
        )
        assert root_alloc.end_date == EXTENDED_END
        for descendant in descendants:
            alloc = _find_test_alloc(
                session, descendant, resource.resource_id, SRC_ACTIVE_AT,
            )
            assert alloc is not None
            assert alloc.end_date == EXTENDED_END

    def test_child_transactions_propagated(self, session):
        root = _require_project(session, 'NMMM0003')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("NMMM0003 has no active descendants")
        resource = _require_resource(session)
        _seed_inheriting_tree(session, root, resource)

        extend_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        descendant = descendants[0]
        alloc = _find_test_alloc(
            session, descendant, resource.resource_id, SRC_ACTIVE_AT,
        )
        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=alloc.allocation_id,
                transaction_type=AllocationTransactionType.EXTENSION,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is True


class TestExtendDivergentTree:

    def test_pushes_standalone_children_end_dates(self, session):
        root = _require_project(session, 'CESM0002')
        descendants = _active_descendants(root)
        if not descendants:
            pytest.skip("CESM0002 has no active descendants")
        resource = _require_resource(session)
        _seed_divergent_tree(session, root, resource)

        extend_project_allocations(
            session,
            root_project_id=root.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        for descendant in descendants:
            alloc = _find_test_alloc(
                session, descendant, resource.resource_id, SRC_ACTIVE_AT,
            )
            assert alloc is not None, f"{descendant.projcode} missing"
            assert alloc.end_date == EXTENDED_END


class TestExtendSkips:

    def test_skips_when_new_end_equals_current(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(session, project, resource)

        updated = extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=SRC_END,                   # same as current
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert updated == []
        assert source.end_date == SRC_END      # unchanged

    def test_skips_when_new_end_before_current(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(session, project, resource)

        shorter = datetime(2099, 6, 30, 23, 59, 59)
        updated = extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=shorter,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert updated == []
        assert source.end_date == SRC_END      # unchanged — no shortening

    def test_skips_open_ended_source(self, session):
        project = _require_project(session, 'SCSG0001')
        resource = _require_resource(session)
        source = _seed_standalone_source(
            session, project, resource,
            start=SRC_START, end=None,         # open-ended
        )

        updated = extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[resource.resource_id],
            user_id=TEST_USER_ID,
        )

        assert updated == []
        assert source.end_date is None         # still open-ended


class TestExtendResourceSelection:

    def test_only_requested_resource_extended(self, session):
        project = _require_project(session, 'SCSG0001')
        derecho = _require_resource(session, 'Derecho')
        casper = _require_resource(session, 'Casper')
        derecho_src = _seed_standalone_source(session, project, derecho)
        casper_src = _seed_standalone_source(session, project, casper)

        extend_project_allocations(
            session,
            root_project_id=project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=TEST_USER_ID,
        )

        assert derecho_src.end_date == EXTENDED_END
        assert casper_src.end_date == SRC_END      # untouched
