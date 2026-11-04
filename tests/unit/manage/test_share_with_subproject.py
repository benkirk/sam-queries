"""share_refusal / share_with_subproject: one sub-project takes a share of its
immediate parent's current allocation (the tree's Share action, #902)."""
from datetime import datetime, timedelta

import pytest

from sam.accounting.accounts import Account
from sam.accounting.allocations import AllocationTransaction, replay_amount
from sam.manage.allocations import share_refusal, share_with_subproject
from sam.manage.transaction import management_transaction

from factories import make_account, make_allocation, make_project, make_resource, make_user


AMOUNT = 16_500_000.0


@pytest.fixture
def tree(session):
    """root (current pool) -> c1, c2; c1 -> grandchild. Only the root holds the resource."""
    now = datetime.now()
    resource = make_resource(session)
    root = make_project(session)
    c1 = make_project(session, parent=root)
    c2 = make_project(session, parent=root)
    grandchild = make_project(session, parent=c1)
    pool = make_allocation(session, account=make_account(session, project=root, resource=resource),
                           amount=AMOUNT, start_date=now - timedelta(days=30),
                           end_date=now + timedelta(days=300))
    return resource, root, c1, c2, grandchild, pool


def _account(session, project, resource):
    return Account.get_by_project_and_resource(session, project.project_id, resource.resource_id)


def _share(session, pool, project):
    with management_transaction(session):
        return share_with_subproject(session, pool, project, make_user(session).user_id)


class TestShareWithSubproject:

    def test_one_inheriting_allocation_and_one_create_row(self, session, tree):
        resource, root, c1, c2, _gc, pool = tree
        alloc = _share(session, pool, c1)

        assert alloc.parent_allocation_id == pool.allocation_id
        assert (alloc.amount, alloc.start_date, alloc.end_date) == (
            pool.amount, pool.start_date, pool.end_date)
        txns = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id).all()
        assert len(txns) == 1 and txns[0].propagated is True
        assert replay_amount(txns) == pytest.approx(alloc.amount)

    def test_siblings_and_grandchildren_untouched(self, session, tree):
        resource, root, c1, c2, grandchild, pool = tree
        _share(session, pool, c1)
        for other in (c2, grandchild):
            account = _account(session, other, resource)
            assert account is None or not account.live_allocations

    def test_deep_tree_links_to_the_immediate_parent(self, session, tree):
        resource, root, c1, c2, grandchild, pool = tree
        middle = _share(session, pool, c1)
        leaf = _share(session, middle, grandchild)
        assert leaf.parent_allocation_id == middle.allocation_id
        assert leaf.amount == pool.amount

    def test_refused_write_raises_and_creates_nothing(self, session, tree):
        resource, root, c1, c2, grandchild, pool = tree
        with pytest.raises(ValueError, match='not a sub-project'):
            _share(session, pool, grandchild)
        account = _account(session, grandchild, resource)
        assert account is None or not account.live_allocations


class TestShareRefusal:

    def test_eligible_child(self, session, tree):
        resource, root, c1, *_rest, pool = tree
        assert share_refusal(pool, c1, None) is None

    def test_not_the_immediate_parent(self, session, tree):
        _r, root, c1, c2, grandchild, pool = tree
        assert 'not a sub-project' in share_refusal(pool, grandchild, None)

    def test_inactive_child(self, session, tree):
        _r, root, c1, *_rest, pool = tree
        c1.active = False
        assert 'inactive' in share_refusal(pool, c1, None)

    def test_expired_pool(self, session, tree):
        _r, root, c1, *_rest, pool = tree
        assert 'not current' in share_refusal(pool, c1, None, now=pool.end_date + timedelta(days=1))

    def test_child_with_any_live_allocation_even_expired(self, session, tree):
        resource, root, c1, *_rest, pool = tree
        old = make_allocation(session, account=make_account(session, project=c1, resource=resource),
                              start_date=datetime.now() - timedelta(days=800),
                              end_date=datetime.now() - timedelta(days=400))
        assert 'already has an allocation' in share_refusal(pool, c1, old.account)

    def test_child_with_only_a_deleted_allocation(self, session, tree):
        resource, root, c1, *_rest, pool = tree
        gone = make_allocation(session, account=make_account(session, project=c1, resource=resource),
                               deleted=True)
        assert share_refusal(pool, c1, gone.account) is None
