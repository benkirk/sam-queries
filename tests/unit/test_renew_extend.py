"""Tests for sam.manage.renew and sam.manage.extend — Phase 3 port.

Ported from tests/unit/test_renew_extend.py. The legacy file pinned three
specific snapshot projects (SCSG0001 standalone, NMMM0003 inheriting tree,
CESM0002 divergent tree) plus the Derecho / Casper resources, then layered
its own test allocations at far-future dates (2099+) on top of them.

The port builds each topology from scratch via factories so the tests
own their entire graph: project tree shape, resources, accounts, and
allocations. The legacy helper functions (_seed_standalone_source,
_seed_inheriting_tree, _seed_divergent_tree) are reused as-is — they were
already factory-pattern code, just operating on snapshot-fetched roots.
"""
from datetime import datetime

import pytest

from sam import Allocation
from sam.accounting.accounts import Account
from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
)
from sam.manage.extend import extend_project_allocations
from sam.manage.renew import (
    analyze_renew_preconditions,
    find_renewable_descendants,
    find_source_alloc_at,
    find_source_allocations_at,
    renew_project_allocations,
)

from factories import make_project, make_resource, make_user

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Constants — far-future dates so tests never collide with real data
# ---------------------------------------------------------------------------

SRC_START = datetime(2099, 1, 1)
SRC_END = datetime(2099, 12, 31, 23, 59, 59)
SRC_ACTIVE_AT = datetime(2099, 6, 15)

NEW_START = datetime(2100, 1, 1)
NEW_END = datetime(2100, 12, 31, 23, 59, 59)

EXTENDED_END = datetime(2100, 6, 30, 23, 59, 59)

ROOT_AMOUNT = 1_000_000.0
CHILD_BASE_AMOUNT = 100_000.0


# ---------------------------------------------------------------------------
# Topology builders
# ---------------------------------------------------------------------------


def _active_descendants(project):
    return [d for d in project.get_descendants() if d.active]


def _seed_standalone_source(session, project, resource, *, amount=ROOT_AMOUNT,
                            start=SRC_START, end=SRC_END):
    """Create a single non-inheriting Allocation on `project` for `resource`."""
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


def _seed_inheriting_tree(session, root, resource, *, amount=ROOT_AMOUNT):
    """Build a root + inheriting children chain over `root.get_descendants()`."""
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


def _seed_divergent_tree(session, root, resource, *, amount=ROOT_AMOUNT):
    """Build a root + standalone (no parent_allocation_id) per-child allocations."""
    root_alloc = _seed_standalone_source(session, root, resource, amount=amount)
    child_allocs: dict[int, tuple[Allocation, float]] = {}
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
    """Bypass relationship caches — query the freshly inserted allocation directly."""
    return (
        session.query(Allocation)
        .join(Account, Allocation.account_id == Account.account_id)
        .filter(
            Account.project_id == project.project_id,
            Account.resource_id == resource_id,
            Allocation.deleted == False,  # noqa: E712
            Allocation.start_date <= active_at,
        )
        .filter(
            (Allocation.end_date == None) | (Allocation.end_date >= active_at)  # noqa: E711
        )
        .order_by(Allocation.start_date.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Fixtures (function-scoped — fresh isolated graph per test)
# ---------------------------------------------------------------------------


@pytest.fixture
def acting_user(session):
    return make_user(session)


@pytest.fixture
def standalone_project(session):
    """A single Project with no children — legacy SCSG0001 stand-in."""
    return make_project(session)


@pytest.fixture
def tree_root_with_children(session):
    """Root Project with 3 active child projects — legacy NMMM0003/CESM0002 stand-in."""
    root = make_project(session)
    for _ in range(3):
        make_project(session, parent=root)
    session.expire_all()
    # Re-fetch root so its NestedSetMixin coordinates reflect the children.
    return session.get(type(root), root.project_id)


@pytest.fixture
def derecho(session):
    """Stand-in for snapshot 'Derecho' resource."""
    return make_resource(session)


@pytest.fixture
def casper(session):
    """Stand-in for snapshot 'Casper' resource — a second distinct resource."""
    return make_resource(session)


# ---------------------------------------------------------------------------
# find_source_alloc_at
# ---------------------------------------------------------------------------


class TestFindSourceAllocAt:

    def test_returns_standalone_source(self, session, standalone_project, derecho):
        seeded = _seed_standalone_source(session, standalone_project, derecho)
        found = find_source_alloc_at(standalone_project, derecho.resource_id, SRC_ACTIVE_AT)
        assert found is not None
        assert found.allocation_id == seeded.allocation_id

    def test_returns_none_when_no_active_source(self, session, standalone_project, derecho):
        _seed_standalone_source(session, standalone_project, derecho)
        out_of_range = datetime(2098, 6, 15)
        assert find_source_alloc_at(standalone_project, derecho.resource_id, out_of_range) is None

    def test_returns_latest_start_when_multiple_active(self, session, standalone_project, derecho):
        _seed_standalone_source(
            session, standalone_project, derecho,
            start=datetime(2099, 1, 1), end=datetime(2099, 12, 31, 23, 59, 59),
        )
        winner = _seed_standalone_source(
            session, standalone_project, derecho,
            start=datetime(2099, 4, 1), end=datetime(2099, 10, 31, 23, 59, 59),
        )
        found = find_source_alloc_at(
            standalone_project, derecho.resource_id, datetime(2099, 6, 1),
        )
        assert found.allocation_id == winner.allocation_id


# ---------------------------------------------------------------------------
# find_source_allocations_at
# ---------------------------------------------------------------------------


class TestFindSourceAllocationsAt:

    def test_excludes_inheriting_allocations(self, session, tree_root_with_children, derecho):
        _seed_inheriting_tree(session, tree_root_with_children, derecho)
        results = find_source_allocations_at(session, tree_root_with_children, SRC_ACTIVE_AT)
        for alloc in results:
            assert not alloc.is_inheriting


# ---------------------------------------------------------------------------
# find_renewable_descendants
# ---------------------------------------------------------------------------


class TestFindRenewableDescendants:

    def test_inheriting_tree_returns_all_active_descendants(
        self, session, tree_root_with_children, derecho,
    ):
        descendants = _active_descendants(tree_root_with_children)
        assert descendants, "fixture must have descendants"
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        found = find_renewable_descendants(
            tree_root_with_children, derecho.resource_id, SRC_ACTIVE_AT,
        )
        found_ids = {d.project_id for d in found}
        assert {d.project_id for d in descendants} <= found_ids

    def test_divergent_tree_returns_standalone_children(
        self, session, tree_root_with_children, derecho,
    ):
        descendants = _active_descendants(tree_root_with_children)
        assert descendants, "fixture must have descendants"
        _seed_divergent_tree(session, tree_root_with_children, derecho)

        found = find_renewable_descendants(
            tree_root_with_children, derecho.resource_id, SRC_ACTIVE_AT,
        )
        found_ids = {d.project_id for d in found}
        assert {d.project_id for d in descendants} <= found_ids


# ---------------------------------------------------------------------------
# renew_project_allocations
# ---------------------------------------------------------------------------


class TestRenewStandalone:

    def test_creates_new_root_allocation(self, session, standalone_project, derecho, acting_user):
        source = _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert len(created) == 1
        new_alloc = created[0]
        assert new_alloc.allocation_id != source.allocation_id
        assert new_alloc.amount == source.amount
        assert new_alloc.start_date == NEW_START
        assert new_alloc.end_date == NEW_END
        assert new_alloc.parent_allocation_id is None

    def test_logs_renew_transaction(self, session, standalone_project, derecho, acting_user):
        _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
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

    def test_invalid_dates_raise(self, session, standalone_project, derecho, acting_user):
        _seed_standalone_source(session, standalone_project, derecho)
        with pytest.raises(ValueError):
            renew_project_allocations(
                session,
                root_project_id=standalone_project.project_id,
                source_active_at=SRC_ACTIVE_AT,
                new_start=NEW_END,   # inverted
                new_end=NEW_START,
                resource_ids=[derecho.resource_id],
                user_id=acting_user.user_id,
            )


class TestRenewInheritingTree:

    def test_creates_new_root_plus_inheriting_children(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        created = renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        assert len(created) == 1
        new_root = created[0]
        assert new_root.parent_allocation_id is None

        for descendant in descendants:
            renewed = _find_test_alloc(
                session, descendant, derecho.resource_id, NEW_START,
            )
            assert renewed is not None, f"{descendant.projcode} missing renewal"
            assert renewed.start_date == NEW_START
            assert renewed.end_date == NEW_END
            assert renewed.amount == ROOT_AMOUNT
            assert renewed.parent_allocation_id is not None

    def test_topology_wired_to_new_root(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        created = renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        new_root = created[0]

        for descendant in descendants:
            renewed = _find_test_alloc(
                session, descendant, derecho.resource_id, NEW_START,
            )
            ancestor = renewed
            seen: set[int] = set()
            while ancestor.parent_allocation_id is not None:
                if ancestor.allocation_id in seen:
                    pytest.fail("parent chain cycles")
                seen.add(ancestor.allocation_id)
                ancestor = session.get(Allocation, ancestor.parent_allocation_id)
            assert ancestor.allocation_id == new_root.allocation_id


class TestRenewDivergentTree:

    def test_preserves_per_node_amounts(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _, child_allocs = _seed_divergent_tree(session, tree_root_with_children, derecho)

        renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        for descendant in descendants:
            _, expected_amount = child_allocs[descendant.project_id]
            renewed = _find_test_alloc(
                session, descendant, derecho.resource_id, NEW_START,
            )
            assert renewed is not None, f"{descendant.projcode} missing renewal"
            assert renewed.amount == expected_amount
            assert renewed.parent_allocation_id is None


class TestRenewResourceSelection:

    def test_only_requested_resource_renewed(
        self, session, standalone_project, derecho, casper, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        _seed_standalone_source(session, standalone_project, casper)

        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        assert len(created) == 1
        assert created[0].account.resource_id == derecho.resource_id

        casper_renewed = _find_test_alloc(
            session, standalone_project, casper.resource_id, NEW_START,
        )
        assert casper_renewed is None


class TestRenewScaling:
    """Per-resource scale multiplier applies uniformly across the tree,
    with amounts rounded to SAM_SIG_FIGS (3) to match the human-defined
    precision of allocation amounts."""

    def test_standalone_default_scale_is_identity(
        self, session, standalone_project, derecho, acting_user,
    ):
        """scales=None is byte-identical to pre-scale behaviour."""
        source = _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales=None,
        )
        assert created[0].amount == source.amount

    def test_standalone_scale_half_rounds_to_sig_figs(
        self, session, standalone_project, derecho, acting_user,
    ):
        """ROOT_AMOUNT=1,000,000 × 0.5 = 500,000 — already 3 sig figs."""
        _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 0.5},
        )
        assert created[0].amount == 500_000.0

    def test_standalone_scale_rounds_to_three_sig_figs(
        self, session, standalone_project, derecho, acting_user,
    ):
        """688M × 0.667 = 458,896,000 → 459,000,000 after sig-fig rounding."""
        _seed_standalone_source(
            session, standalone_project, derecho, amount=688_000_000.0,
        )
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 0.667},
        )
        assert created[0].amount == 459_000_000.0

    def test_divergent_tree_scales_uniformly_across_all_nodes(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        """Scale applies to root AND every descendant; each is rounded
        independently to 3 sig figs."""
        descendants = _active_descendants(tree_root_with_children)
        _, child_allocs = _seed_divergent_tree(
            session, tree_root_with_children, derecho,
        )

        renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 0.5},
        )

        # Root: ROOT_AMOUNT * 0.5 = 500,000 (already 3 sig figs)
        root_renewed = _find_test_alloc(
            session, tree_root_with_children, derecho.resource_id, NEW_START,
        )
        assert root_renewed.amount == ROOT_AMOUNT * 0.5

        # Each descendant: source * 0.5, rounded to 3 sig figs
        from sam.fmt import round_to_sig_figs

        for descendant in descendants:
            _, src_amount = child_allocs[descendant.project_id]
            renewed = _find_test_alloc(
                session, descendant, derecho.resource_id, NEW_START,
            )
            expected = round_to_sig_figs(src_amount * 0.5)
            assert renewed.amount == expected, (
                f"{descendant.projcode}: expected {expected}, got {renewed.amount}"
            )

    def test_per_resource_scales_independent(
        self, session, standalone_project, derecho, casper, acting_user,
    ):
        """Different resources can carry different scale factors in one call."""
        _seed_standalone_source(session, standalone_project, derecho)
        _seed_standalone_source(session, standalone_project, casper)

        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id, casper.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 0.5, casper.resource_id: 1.0},
        )

        by_resource = {a.account.resource_id: a for a in created}
        assert by_resource[derecho.resource_id].amount == 500_000.0
        assert by_resource[casper.resource_id].amount == ROOT_AMOUNT

    def test_missing_scale_defaults_to_one(
        self, session, standalone_project, derecho, acting_user,
    ):
        """A resource with no scale entry keeps its source amount verbatim."""
        source = _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={},  # empty → default 1.0 per resource
        )
        assert created[0].amount == source.amount

    def test_scale_suffix_in_transaction_log(
        self, session, standalone_project, derecho, acting_user,
    ):
        """Scaled renewals leave a `— scaled ×N` breadcrumb in the txn log."""
        _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 0.667},
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
        assert '— scaled ×0.667' in txn.transaction_comment

    def test_scale_one_omits_suffix(
        self, session, standalone_project, derecho, acting_user,
    ):
        """scale=1.0 renewals leave no suffix (legacy byte-parity)."""
        _seed_standalone_source(session, standalone_project, derecho)
        created = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            scales={derecho.resource_id: 1.0},
        )
        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=created[0].allocation_id,
                transaction_type=AllocationTransactionType.RENEW,
            )
            .first()
        )
        assert 'scaled' not in txn.transaction_comment


class TestRenewIdempotency:

    def test_second_call_is_noop(self, session, standalone_project, derecho, acting_user):
        _seed_standalone_source(session, standalone_project, derecho)

        first = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()
        second = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert len(first) == 1
        assert len(second) == 0


# ---------------------------------------------------------------------------
# analyze_renew_preconditions
# ---------------------------------------------------------------------------


class TestAnalyzeRenewPreconditions:

    def test_ok_when_source_exists_and_no_overlap(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        result = analyze_renew_preconditions(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
        )
        assert result == {derecho.resource_id: 'ok'}

    def test_no_source_when_resource_never_allocated(
        self, session, standalone_project, derecho, casper,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        result = analyze_renew_preconditions(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[casper.resource_id],
        )
        assert result == {casper.resource_id: 'no_source'}

    def test_overlap_after_prior_renew(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()
        result = analyze_renew_preconditions(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
        )
        assert result == {derecho.resource_id: 'overlap'}

    def test_mixed_statuses_across_resources(
        self, session, standalone_project, derecho, casper, acting_user,
    ):
        # derecho: source exists AND already renewed → 'overlap'
        # casper:  no source at all → 'no_source'
        _seed_standalone_source(session, standalone_project, derecho)
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()

        # Third resource where source exists but no overlap → 'ok'
        other = make_resource(session)
        _seed_standalone_source(session, standalone_project, other)
        session.expire_all()

        result = analyze_renew_preconditions(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id, casper.resource_id, other.resource_id],
        )
        assert result == {
            derecho.resource_id: 'overlap',
            casper.resource_id: 'no_source',
            other.resource_id: 'ok',
        }


# ---------------------------------------------------------------------------
# renew_project_allocations(replace_existing=True)
# ---------------------------------------------------------------------------


class TestRenewReplaceExisting:

    def test_default_skips_overlap(self, session, standalone_project, derecho, acting_user):
        """Baseline: without replace_existing, the second call still no-ops.

        Guards against regression of the silent-skip behavior that keeps
        double-click idempotent when replace_existing is left at its default.
        """
        _seed_standalone_source(session, standalone_project, derecho)
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()
        second = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            # replace_existing defaults to False
        )
        assert len(second) == 0

    def test_replace_true_creates_new_row_when_overlap(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        first = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()
        second = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            replace_existing=True,
        )
        assert len(second) == 1
        assert second[0].allocation_id != first[0].allocation_id

    def test_replace_true_soft_deletes_old_row(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        first = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        old_id = first[0].allocation_id
        session.expire_all()
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            replace_existing=True,
        )
        old = session.get(Allocation, old_id)
        # Row still exists — soft delete only.
        assert old is not None
        assert old.deleted is True

    def test_replace_true_logs_delete_transaction_on_old(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        first = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        old_id = first[0].allocation_id
        session.expire_all()
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            replace_existing=True,
        )
        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=old_id,
                transaction_type=AllocationTransactionType.DELETE,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert 'Superseded by renew' in (txn.transaction_comment or '')

    def test_replace_true_supersedes_in_inheriting_tree(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        # First renew — creates the period we'll collide with.
        renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        session.expire_all()

        # Replace — every descendant's overlapping row should also be soft-deleted.
        renew_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            replace_existing=True,
        )
        session.expire_all()

        for descendant in descendants:
            # Exactly one non-deleted allocation should exist for [NEW_START, NEW_END].
            rows = (
                session.query(Allocation)
                .join(Account, Allocation.account_id == Account.account_id)
                .filter(
                    Account.project_id == descendant.project_id,
                    Account.resource_id == derecho.resource_id,
                    Allocation.deleted == False,   # noqa: E712
                    Allocation.start_date == NEW_START,
                    Allocation.end_date == NEW_END,
                )
                .all()
            )
            assert len(rows) == 1, f"{descendant.projcode} expected 1 live row, got {len(rows)}"
            # And at least one soft-deleted row from the superseded renew.
            deleted_rows = (
                session.query(Allocation)
                .join(Account, Allocation.account_id == Account.account_id)
                .filter(
                    Account.project_id == descendant.project_id,
                    Account.resource_id == derecho.resource_id,
                    Allocation.deleted == True,    # noqa: E712
                )
                .all()
            )
            assert len(deleted_rows) >= 1, f"{descendant.projcode} missing soft-deleted row"

    def test_replace_false_does_not_touch_existing(
        self, session, standalone_project, derecho, acting_user,
    ):
        _seed_standalone_source(session, standalone_project, derecho)
        first = renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        old_id = first[0].allocation_id
        session.expire_all()
        renew_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_start=NEW_START,
            new_end=NEW_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
            replace_existing=False,
        )
        old = session.get(Allocation, old_id)
        assert old.deleted is False


# ---------------------------------------------------------------------------
# extend_project_allocations
# ---------------------------------------------------------------------------


class TestExtendStandalone:

    def test_pushes_root_end_date(self, session, standalone_project, derecho, acting_user):
        source = _seed_standalone_source(session, standalone_project, derecho)
        updated = extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert len(updated) == 1
        assert updated[0].allocation_id == source.allocation_id
        assert source.end_date == EXTENDED_END

    def test_logs_extension_transaction(self, session, standalone_project, derecho, acting_user):
        source = _seed_standalone_source(session, standalone_project, derecho)
        extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
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

    def test_pushes_root_and_all_children(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        extend_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        root_alloc = _find_test_alloc(
            session, tree_root_with_children, derecho.resource_id, SRC_ACTIVE_AT,
        )
        assert root_alloc.end_date == EXTENDED_END
        for descendant in descendants:
            alloc = _find_test_alloc(
                session, descendant, derecho.resource_id, SRC_ACTIVE_AT,
            )
            assert alloc is not None
            assert alloc.end_date == EXTENDED_END

    def test_child_transactions_propagated(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_inheriting_tree(session, tree_root_with_children, derecho)

        extend_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        descendant = descendants[0]
        alloc = _find_test_alloc(
            session, descendant, derecho.resource_id, SRC_ACTIVE_AT,
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

    def test_pushes_standalone_children_end_dates(
        self, session, tree_root_with_children, derecho, acting_user,
    ):
        descendants = _active_descendants(tree_root_with_children)
        _seed_divergent_tree(session, tree_root_with_children, derecho)

        extend_project_allocations(
            session,
            root_project_id=tree_root_with_children.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        for descendant in descendants:
            alloc = _find_test_alloc(
                session, descendant, derecho.resource_id, SRC_ACTIVE_AT,
            )
            assert alloc is not None, f"{descendant.projcode} missing"
            assert alloc.end_date == EXTENDED_END


class TestExtendSkips:

    def test_skips_when_new_end_equals_current(
        self, session, standalone_project, derecho, acting_user,
    ):
        source = _seed_standalone_source(session, standalone_project, derecho)
        updated = extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=SRC_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert updated == []
        assert source.end_date == SRC_END

    def test_skips_when_new_end_before_current(
        self, session, standalone_project, derecho, acting_user,
    ):
        source = _seed_standalone_source(session, standalone_project, derecho)
        shorter = datetime(2099, 6, 30, 23, 59, 59)
        updated = extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=shorter,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert updated == []
        assert source.end_date == SRC_END

    def test_skips_open_ended_source(
        self, session, standalone_project, derecho, acting_user,
    ):
        source = _seed_standalone_source(
            session, standalone_project, derecho, end=None,
        )
        updated = extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )
        assert updated == []
        assert source.end_date is None


class TestExtendResourceSelection:

    def test_only_requested_resource_extended(
        self, session, standalone_project, derecho, casper, acting_user,
    ):
        derecho_src = _seed_standalone_source(session, standalone_project, derecho)
        casper_src = _seed_standalone_source(session, standalone_project, casper)

        extend_project_allocations(
            session,
            root_project_id=standalone_project.project_id,
            source_active_at=SRC_ACTIVE_AT,
            new_end=EXTENDED_END,
            resource_ids=[derecho.resource_id],
            user_id=acting_user.user_id,
        )

        assert derecho_src.end_date == EXTENDED_END
        assert casper_src.end_date == SRC_END
