"""Smoke tests for new_tests/factories/ — Layer 2 builder functions.

These verify that each factory:
  - returns an instance with its primary key populated after `session.flush()`,
  - populates required FK columns,
  - auto-builds the dependency graph when the caller doesn't supply parents.

Validates the factory module in isolation before any port depends on it.
"""
import pytest

from factories import (
    make_account,
    make_allocation,
    make_aoi,
    make_aoi_group,
    make_facility,
    make_machine,
    make_organization,
    make_project,
    make_queue,
    make_resource,
    make_resource_type,
    make_user,
    make_wallclock_exemption,
)

pytestmark = pytest.mark.unit


class TestCoreFactories:

    def test_make_organization_assigns_pk(self, session):
        org = make_organization(session)
        assert org.organization_id is not None
        assert org.acronym.startswith("ORG")
        assert org.name

    def test_make_organization_unique_pks(self, session):
        a = make_organization(session)
        b = make_organization(session)
        assert a.organization_id != b.organization_id
        assert a.acronym != b.acronym

    def test_make_user_assigns_pk(self, session):
        user = make_user(session)
        assert user.user_id is not None
        assert user.username.startswith("usr")
        assert user.unix_uid is not None
        assert user.active is True

    def test_make_user_unique_usernames(self, session):
        a = make_user(session)
        b = make_user(session)
        assert a.username != b.username


class TestResourceFactories:

    def test_make_resource_type_assigns_pk(self, session):
        rt = make_resource_type(session)
        assert rt.resource_type_id is not None
        assert rt.resource_type.startswith("RT")

    def test_make_resource_auto_builds_resource_type(self, session):
        res = make_resource(session)
        assert res.resource_id is not None
        assert res.resource_type_id is not None
        assert res.resource_name.startswith("RES")

    def test_make_resource_accepts_explicit_resource_type(self, session):
        rt = make_resource_type(session)
        res = make_resource(session, resource_type=rt)
        assert res.resource_type_id == rt.resource_type_id

    def test_make_machine_auto_builds_resource(self, session):
        m = make_machine(session)
        assert m.machine_id is not None
        assert m.resource_id is not None
        assert m.name.startswith("mach")

    def test_make_queue_auto_builds_resource(self, session):
        q = make_queue(session)
        assert q.queue_id is not None
        assert q.resource_id is not None
        assert q.queue_name.startswith("q")
        assert q.description == "test queue"  # NOT NULL default

    def test_make_queue_accepts_explicit_resource(self, session):
        res = make_resource(session)
        q = make_queue(session, resource=res)
        assert q.resource_id == res.resource_id


class TestOperationalFactories:

    def test_make_wallclock_exemption_auto_builds_user_and_queue(self, session):
        ex = make_wallclock_exemption(session)
        assert ex.wallclock_exemption_id is not None
        assert ex.user_id is not None
        assert ex.queue_id is not None
        assert ex.time_limit_hours == 24.0
        assert ex.comment is None

    def test_make_wallclock_exemption_with_explicit_user_and_queue(self, session):
        user = make_user(session)
        queue = make_queue(session)
        ex = make_wallclock_exemption(
            session, user=user, queue=queue, time_limit_hours=72.0, comment="rationale"
        )
        assert ex.user_id == user.user_id
        assert ex.queue_id == queue.queue_id
        assert ex.time_limit_hours == 72.0
        assert ex.comment == "rationale"


class TestProjectFactories:

    def test_make_facility_assigns_pk(self, session):
        f = make_facility(session)
        assert f.facility_id is not None
        assert f.facility_name.startswith("F")
        assert f.code is None  # nullable, 1-char unique → factory leaves None
        assert f.description

    def test_make_aoi_group_assigns_pk(self, session):
        g = make_aoi_group(session)
        assert g.area_of_interest_group_id is not None
        assert g.name.startswith("AOIG")

    def test_make_aoi_auto_builds_group(self, session):
        a = make_aoi(session)
        assert a.area_of_interest_id is not None
        assert a.area_of_interest_group_id is not None
        assert a.area_of_interest.startswith("AOI")

    def test_make_project_auto_builds_lead_and_aoi(self, session):
        from sam.accounting.accounts import AccountUser
        p = make_project(session)
        assert p.project_id is not None
        assert p.projcode.startswith("PRJ")
        assert p.project_lead_user_id is not None
        assert p.area_of_interest_id is not None
        # NestedSetMixin .add() sets root-tree coordinates.
        assert p.tree_left == 1
        assert p.tree_right == 2
        assert p.tree_root == p.project_id
        # Brand-new project has no accounts yet.
        assert p.accounts == []
        # And no AccountUser rows yet (Account.create() is what propagates lead).
        assert session.query(AccountUser).filter_by(user_id=p.project_lead_user_id).count() == 0

    def test_make_project_child_under_parent(self, session):
        root = make_project(session)
        child = make_project(session, parent=root)
        assert child.parent_id == root.project_id
        assert child.tree_root == root.tree_root  # shares root with parent
        # parent_id is set via NestedSetMixin? Let's verify via the relationship...
        session.refresh(root)
        assert child in root.children

    def test_make_account_auto_builds_project_and_resource(self, session):
        acct = make_account(session)
        assert acct.account_id is not None
        assert acct.project_id is not None
        assert acct.resource_id is not None
        # Account.create() propagated the project lead as an AccountUser.
        assert any(
            au.user_id == acct.project.project_lead_user_id for au in acct.users
        )

    def test_make_account_explicit_project_and_resource(self, session):
        project = make_project(session)
        resource = make_resource(session)
        acct = make_account(session, project=project, resource=resource)
        assert acct.project_id == project.project_id
        assert acct.resource_id == resource.resource_id

    def test_make_allocation_auto_builds_account(self, session):
        alloc = make_allocation(session)
        assert alloc.allocation_id is not None
        assert alloc.account_id is not None
        assert alloc.amount == 10_000.0
        assert alloc.deleted is False
        assert alloc.is_active is True

    def test_make_allocation_with_explicit_account(self, session):
        account = make_account(session)
        alloc = make_allocation(session, account=account, amount=50_000.0)
        assert alloc.account_id == account.account_id
        assert alloc.amount == 50_000.0

    def test_make_allocation_child_of_parent(self, session):
        account = make_account(session)
        parent = make_allocation(session, account=account, amount=100_000.0)
        child = make_allocation(session, account=account, amount=25_000.0, parent=parent)
        assert child.parent_allocation_id == parent.allocation_id
        assert child in parent.children
