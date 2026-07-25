"""Tests for the allocate-down (residual sub-allocation) flow:

- ``AllocateResidualForm`` (sam.schemas.forms.user): composite ``target``
  parsing + amount validation.
- ``allocate_residual_to_child()`` (sam.manage.allocations): assign part of a
  parent allocation's carve-out residual to a sub-project — bump an existing
  frontier carve-out, or create a new standalone allocation on an uncovered
  branch. The parent's amount NEVER changes and gets NO transaction row
  (replay invariant: replay(history) == amount must keep holding for every
  touched allocation).
"""
from datetime import datetime, timedelta

import pytest
from marshmallow import ValidationError

from sam.accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
    InheritingAllocationException,
    replay_amount,
)
from sam.manage.allocations import allocate_residual_to_child, get_carveout_frontier
from sam.schemas.forms import AllocateResidualForm

from factories import (
    make_account,
    make_allocation,
    make_allocation_transaction,
    make_project,
    make_resource,
    make_user,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Form schema
# ---------------------------------------------------------------------------


class TestAllocateResidualForm:

    def test_alloc_target_coerced(self):
        data = AllocateResidualForm().load({'target': 'alloc:12', 'amount': '100'})
        assert data['target_allocation_id'] == 12
        assert data['target_project_id'] is None
        assert data['amount'] == 100.0

    def test_proj_target_coerced(self):
        data = AllocateResidualForm().load({'target': 'proj:7', 'amount': '5.5'})
        assert data['target_allocation_id'] is None
        assert data['target_project_id'] == 7

    def test_bad_target_format_rejected(self):
        for bad in ('bogus', 'alloc:', 'proj:x', 'alloc:1;drop', ''):
            with pytest.raises(ValidationError):
                AllocateResidualForm().load({'target': bad, 'amount': '1'})

    def test_missing_target_rejected(self):
        with pytest.raises(ValidationError) as ei:
            AllocateResidualForm().load({'amount': '1'})
        assert 'target' in ei.value.messages

    def test_zero_and_negative_amount_rejected(self):
        for bad in ('0', '-10'):
            with pytest.raises(ValidationError) as ei:
                AllocateResidualForm().load({'target': 'alloc:1', 'amount': bad})
            assert 'amount' in ei.value.messages

    def test_unknown_fields_dropped(self):
        data = AllocateResidualForm().load(
            {'target': 'alloc:1', 'amount': '1', 'csrf_token': 'x'})
        assert 'csrf_token' not in data

    def test_comment_optional(self):
        data = AllocateResidualForm().load({'target': 'alloc:1', 'amount': '1'})
        assert data['comment'] is None


# ---------------------------------------------------------------------------
# allocate_residual_to_child
# ---------------------------------------------------------------------------


@pytest.fixture
def acting_user(session):
    return make_user(session)


@pytest.fixture
def carve_tree(session):
    """Parent allocation 1M with a mixed frontier.

    Returns dict with:
      parent_alloc  — 1M standalone on the root project
      carve_alloc   — 300k standalone on child C1 (carve-out; bump target)
      pool_alloc    — 1M linked on child C3 (pool member; invalid target)
      open_project  — child C2 with no allocation (create target)
      resource
    """
    resource = make_resource(session)
    root = make_project(session)
    root_account = make_account(session, project=root, resource=resource)
    start = datetime.now() - timedelta(days=30)
    end = datetime.now() + timedelta(days=365)
    parent_alloc = make_allocation(
        session, account=root_account, amount=1_000_000.0,
        start_date=start, end_date=end,
    )

    c1 = make_project(session, parent=root)
    carve_alloc = make_allocation(
        session, account=make_account(session, project=c1, resource=resource),
        amount=300_000.0, start_date=start, end_date=end,
    )

    open_project = make_project(session, parent=root)   # C2 — no allocation

    c3 = make_project(session, parent=root)
    pool_alloc = make_allocation(
        session, account=make_account(session, project=c3, resource=resource),
        amount=1_000_000.0, start_date=start, end_date=end,
        parent=parent_alloc,
    )

    return {
        'parent_alloc': parent_alloc,
        'carve_alloc': carve_alloc,
        'pool_alloc': pool_alloc,
        'open_project': open_project,
        'resource': resource,
    }


def _txns(session, allocation_id):
    return (
        session.query(AllocationTransaction)
        .filter_by(allocation_id=allocation_id)
        .all()
    )


class TestBumpPath:

    def test_bump_increases_child_parent_unchanged(self, session, carve_tree, acting_user):
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        result = allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=100_000.0, target_allocation_id=target.allocation_id,
        )
        session.refresh(parent)
        session.refresh(target)
        assert result.allocation_id == target.allocation_id
        assert target.amount == 400_000.0
        assert parent.amount == 1_000_000.0            # NEVER changes
        # Residual shrank implicitly: 1M − 400k = 600k.
        assert get_carveout_frontier(session, parent).residual == 600_000.0

    def test_bump_audit_rows(self, session, carve_tree, acting_user):
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=100_000.0, target_allocation_id=target.allocation_id,
            comment='FY27 supplement',
        )
        # Parent gets NO row — any additive row would corrupt legacy replay.
        assert _txns(session, parent.allocation_id) == []
        rows = _txns(session, target.allocation_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.transaction_type == AllocationTransactionType.ADJUSTMENT
        assert row.transaction_amount == 100_000.0     # signed delta
        assert 'Sub-allocated' in row.transaction_comment
        assert 'FY27 supplement' in row.transaction_comment
        assert not row.transaction_comment.startswith('[')   # no [TAG]

    def test_bump_replay_invariant(self, session, carve_tree, acting_user):
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        # Seed the NEW baseline create_allocation would have written.
        make_allocation_transaction(
            session, allocation=target, user=acting_user,
            transaction_type=AllocationTransactionType.NEW,
            transaction_amount=target.amount,
        )
        allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=100_000.0, target_allocation_id=target.allocation_id,
        )
        session.refresh(target)
        assert replay_amount(_txns(session, target.allocation_id)) == pytest.approx(
            target.amount)

    def test_bump_cascades_to_shared_children_of_target(self, session, carve_tree, acting_user):
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        # A shared (linked) copy under the target — must follow the bump.
        linked_child = make_allocation(
            session, account=target.account, amount=target.amount, parent=target,
        )
        allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=50_000.0, target_allocation_id=target.allocation_id,
        )
        session.refresh(target)
        session.refresh(linked_child)
        assert linked_child.amount == target.amount == 350_000.0
        child_rows = _txns(session, linked_child.allocation_id)
        assert len(child_rows) == 1
        assert child_rows[0].propagated is True


class TestCreatePath:

    def test_create_standalone_with_parent_dates(self, session, carve_tree, acting_user):
        parent = carve_tree['parent_alloc']
        open_project = carve_tree['open_project']
        child = allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=200_000.0, target_project_id=open_project.project_id,
        )
        assert child.parent_allocation_id is None       # standalone carve-out
        assert child.amount == 200_000.0
        assert child.account.project_id == open_project.project_id
        assert child.account.resource_id == parent.account.resource_id
        assert child.start_date == parent.start_date
        assert child.end_date == parent.end_date
        session.refresh(parent)
        assert parent.amount == 1_000_000.0
        # New carve joins the frontier: residual 1M − 300k − 200k = 500k.
        assert get_carveout_frontier(session, parent).residual == 500_000.0

    def test_create_audit_row_replays(self, session, carve_tree, acting_user):
        parent = carve_tree['parent_alloc']
        child = allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=200_000.0, target_project_id=carve_tree['open_project'].project_id,
        )
        rows = _txns(session, child.allocation_id)
        assert len(rows) == 1
        assert rows[0].transaction_type == 'NEW'        # CREATE → legacy NEW
        assert rows[0].transaction_amount == 200_000.0
        assert 'Sub-allocated' in rows[0].transaction_comment
        assert replay_amount(rows) == pytest.approx(child.amount)
        assert _txns(session, parent.allocation_id) == []


class TestValidation:

    def test_overdraft_rejected(self, session, carve_tree, acting_user):
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        with pytest.raises(ValueError, match='exceeds the unallocated residual'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=700_001.0,                       # residual is 700k
                target_allocation_id=target.allocation_id,
            )
        session.refresh(target)
        assert target.amount == 300_000.0
        assert _txns(session, target.allocation_id) == []

    def test_full_residual_allowed_when_result_stays_distinct(self, session, acting_user):
        """Allocating the entire residual is fine as long as the resulting
        child amount stays distinct from the parent's."""
        resource = make_resource(session)
        root = make_project(session)
        parent = make_allocation(
            session, account=make_account(session, project=root, resource=resource),
            amount=1_000_000.0,
        )
        c1 = make_project(session, parent=root)
        c2 = make_project(session, parent=root)
        target = make_allocation(
            session, account=make_account(session, project=c1, resource=resource),
            amount=300_000.0,
        )
        make_allocation(
            session, account=make_account(session, project=c2, resource=resource),
            amount=200_000.0,
        )
        # residual = 1M − 500k = 500k; bump target to 800k ≠ 1M → allowed.
        allocate_residual_to_child(
            session, parent.allocation_id, acting_user.user_id,
            amount=500_000.0, target_allocation_id=target.allocation_id,
        )
        session.refresh(parent)
        assert get_carveout_frontier(session, parent).residual == 0.0

    def test_bump_to_exact_parent_amount_rejected(self, session, carve_tree, acting_user):
        """Landing on exactly the parent's amount would flip the carve-out
        into a pool member (equal-amount fallback) — refused as ambiguous."""
        parent, target = carve_tree['parent_alloc'], carve_tree['carve_alloc']
        # target 300k + 700k == parent 1M → ambiguous end state.
        with pytest.raises(ValueError, match='shared-pool member'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=700_000.0, target_allocation_id=target.allocation_id,
            )
        session.refresh(target)
        assert target.amount == 300_000.0

    def test_create_at_exact_parent_amount_rejected(self, session, acting_user):
        resource = make_resource(session)
        root = make_project(session)
        parent = make_allocation(
            session, account=make_account(session, project=root, resource=resource),
            amount=1_000_000.0,
        )
        bare = make_project(session, parent=root)
        with pytest.raises(ValueError, match='shared-pool member'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1_000_000.0, target_project_id=bare.project_id,
            )

    def test_over_carved_parent_rejected(self, session, acting_user):
        resource = make_resource(session)
        root = make_project(session)
        parent = make_allocation(
            session, account=make_account(session, project=root, resource=resource),
            amount=1_000_000.0,
        )
        c1 = make_project(session, parent=root)
        target = make_allocation(
            session, account=make_account(session, project=c1, resource=resource),
            amount=1_200_000.0,                          # deficit
        )
        with pytest.raises(ValueError, match='exceed'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1.0, target_allocation_id=target.allocation_id,
            )

    def test_pool_member_target_rejected(self, session, carve_tree, acting_user):
        parent, pool = carve_tree['parent_alloc'], carve_tree['pool_alloc']
        with pytest.raises(ValueError, match='not a carve-out'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1.0, target_allocation_id=pool.allocation_id,
            )

    def test_equal_amount_unlinked_target_rejected(self, session, acting_user):
        """An equal-amount unlinked child classifies as a pool member — bumping
        it would silently flip it to a carve-out, so it is not a valid target."""
        resource = make_resource(session)
        root = make_project(session)
        parent = make_allocation(
            session, account=make_account(session, project=root, resource=resource),
            amount=1_000_000.0,
        )
        child = make_project(session, parent=root)
        same_amount = make_allocation(
            session, account=make_account(session, project=child, resource=resource),
            amount=1_000_000.0,                          # equal — pool fallback
        )
        with pytest.raises(ValueError, match='not a carve-out'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1.0, target_allocation_id=same_amount.allocation_id,
            )

    def test_non_frontier_grandchild_target_rejected(self, session, carve_tree, acting_user):
        """A grandchild allocation under an allocated child draws from the
        child — it is not on the parent's frontier."""
        parent = carve_tree['parent_alloc']
        c1_project = carve_tree['carve_alloc'].account.project
        gc = make_project(session, parent=c1_project)
        gc_alloc = make_allocation(
            session,
            account=make_account(session, project=gc,
                                 resource=carve_tree['resource']),
            amount=50_000.0,
        )
        with pytest.raises(ValueError, match='not a carve-out'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1.0, target_allocation_id=gc_alloc.allocation_id,
            )

    def test_covered_branch_project_not_create_target(self, session, carve_tree, acting_user):
        """A bare project under an allocated child is served by the child's
        own frontier — not creatable from the grandparent."""
        parent = carve_tree['parent_alloc']
        c1_project = carve_tree['carve_alloc'].account.project
        bare_gc = make_project(session, parent=c1_project)
        with pytest.raises(ValueError, match='not an uncovered'):
            allocate_residual_to_child(
                session, parent.allocation_id, acting_user.user_id,
                amount=1.0, target_project_id=bare_gc.project_id,
            )

    def test_inheriting_parent_rejected(self, session, carve_tree, acting_user):
        pool = carve_tree['pool_alloc']                  # linked copy
        with pytest.raises(InheritingAllocationException):
            allocate_residual_to_child(
                session, pool.allocation_id, acting_user.user_id,
                amount=1.0, target_project_id=carve_tree['open_project'].project_id,
            )

    def test_both_targets_rejected(self, session, carve_tree, acting_user):
        with pytest.raises(ValueError, match='Exactly one'):
            allocate_residual_to_child(
                session, carve_tree['parent_alloc'].allocation_id,
                acting_user.user_id, amount=1.0,
                target_allocation_id=carve_tree['carve_alloc'].allocation_id,
                target_project_id=carve_tree['open_project'].project_id,
            )

    def test_neither_target_rejected(self, session, carve_tree, acting_user):
        with pytest.raises(ValueError, match='Exactly one'):
            allocate_residual_to_child(
                session, carve_tree['parent_alloc'].allocation_id,
                acting_user.user_id, amount=1.0,
            )

    def test_nonpositive_amount_rejected(self, session, carve_tree, acting_user):
        for bad in (0.0, -5.0):
            with pytest.raises(ValueError, match='positive'):
                allocate_residual_to_child(
                    session, carve_tree['parent_alloc'].allocation_id,
                    acting_user.user_id, amount=bad,
                    target_allocation_id=carve_tree['carve_alloc'].allocation_id,
                )

    def test_missing_parent_rejected(self, session, carve_tree, acting_user):
        with pytest.raises(ValueError, match='not found'):
            allocate_residual_to_child(
                session, 99_999_999, acting_user.user_id,
                amount=1.0,
                target_allocation_id=carve_tree['carve_alloc'].allocation_id,
            )
