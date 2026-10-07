"""The Allocations tab's "Active at" picker sees inactive projects.

A node shows by project flag OR by holding a displayed allocation at the viewed
date (`_visible_tree_nodes`); the shared `project_tree_rows` macro takes that set
as `visible=`. Route tests are render smoke on snapshot rows (routes read committed
rows only); the predicate is tested with factories on the SAVEPOINT session.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from factories import make_account, make_allocation, make_project, make_resource
from sam.queries.dashboard import build_user_projects_resources_batched
from webapp.dashboards.admin.projects_routes import _visible_tree_nodes

INSIDE = datetime(2026, 5, 2)
AFTER_GRACE = datetime(2026, 10, 7)        # 99 days past END: outside the 90-day window
START, END = datetime(2024, 7, 1), datetime(2026, 6, 30)


def _reload(session, project):
    session.expire_all()
    return session.get(type(project), project.project_id)


def _tree(root):
    return [root] + root.get_descendants()


def _visible(session, root, active_at):
    tree = _tree(root)
    by_project = build_user_projects_resources_batched(session, tree, active_at=active_at)
    return {n.projcode for n in _visible_tree_nodes(root, tree, by_project)}, by_project


class TestVisibleTreeNodes:

    def test_inactive_root_is_always_in_and_holds_a_row_inside_its_window(self, session):
        root = make_project(session, active=False)
        make_allocation(session, account=make_account(session, project=root, resource=make_resource(session)),
                        start_date=START, end_date=END)
        root = _reload(session, root)

        shown, by_project = _visible(session, root, INSIDE)
        assert shown == {root.projcode}
        assert [r['allocation_id'] for r in by_project[root.project_id]]

        shown, by_project = _visible(session, root, AFTER_GRACE)
        assert shown == {root.projcode}
        assert by_project[root.project_id] == []

    def test_inactive_child_shows_only_while_it_holds_a_row(self, session):
        root = make_project(session)
        child = make_project(session, parent=root, active=False)
        make_allocation(session, account=make_account(session, project=child, resource=make_resource(session)),
                        start_date=START, end_date=END)
        root = _reload(session, root)

        shown, _ = _visible(session, root, INSIDE)
        assert shown == {root.projcode, child.projcode}
        shown, _ = _visible(session, root, AFTER_GRACE)
        assert shown == {root.projcode}

    def test_inactive_ancestor_without_a_row_is_kept_so_a_shown_leaf_nests(self, session):
        root = make_project(session)
        middle = make_project(session, parent=root, active=False)
        leaf = make_project(session, parent=middle, active=False)
        make_allocation(session, account=make_account(session, project=leaf, resource=make_resource(session)),
                        start_date=START, end_date=END)
        root = _reload(session, root)

        shown, _ = _visible(session, root, INSIDE)
        assert shown == {root.projcode, middle.projcode, leaf.projcode}
        shown, _ = _visible(session, root, AFTER_GRACE)
        assert shown == {root.projcode}

    def test_active_child_without_any_allocation_still_shows(self, session):
        root = make_project(session)
        child = make_project(session, parent=root)
        root = _reload(session, root)
        shown, _ = _visible(session, root, AFTER_GRACE)
        assert shown == {root.projcode, child.projcode}


def _node(projcode, active=True, children=()):
    return SimpleNamespace(projcode=projcode, title='', active=active,
                           children=list(children), parent_id=None, project_id=None)


class TestProjectTreeRowsVisible:
    """`visible=` is the whole rule; an admitted inactive row is muted and tagged."""

    @pytest.fixture
    def rows(self, app):
        def render(**kwargs):
            with app.test_request_context():
                tpl = app.jinja_env.get_template('dashboards/shared/project_tree.html')
                return tpl.module.project_tree_rows(**kwargs)
        return render

    def test_visible_set_admits_an_inactive_node_and_marks_it(self, rows):
        root = _node('A', children=[_node('A1', active=False), _node('A2', active=False)])
        html = rows(nodes=[root], visible={'A', 'A1'})
        assert 'A1' in html and 'A2' not in html
        assert 'row-inactive' in html
        assert 'state-tag' in html and '>inactive<' in html

    def test_without_visible_active_only_still_hides_inactive(self, rows):
        root = _node('A', children=[_node('A1', active=False), _node('A2')])
        html = rows(nodes=[root], active_only=True)
        assert 'A2' in html and 'A1' not in html
        assert 'row-inactive' not in html


@pytest.fixture
def inactive_project_with_allocation(session):
    """ANY inactive snapshot project holding a live, dated allocation: (project, allocation, resource_name)."""
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    from sam.projects.projects import Project
    from sam.resources.resources import Resource

    row = (
        session.query(Project, Allocation, Resource.resource_name)
        .join(Account, Account.project_id == Project.project_id)
        .join(Allocation, Allocation.account_id == Account.account_id)
        .join(Resource, Resource.resource_id == Account.resource_id)
        .filter(~Project.is_active, Account.is_active,
                Allocation.deleted == False,  # noqa: E712 — not-deleted regardless of date
                Allocation.start_date.isnot(None), Allocation.end_date.isnot(None),
                Allocation.end_date > Allocation.start_date + timedelta(days=2))
        .order_by(Project.project_id)
        .first()
    )
    if row is None:
        pytest.skip("No inactive project with a dated allocation in database")
    return row


def _tree_fragment(auth_client, projcode, query=''):
    resp = auth_client.get(f'/admin/htmx/project-allocation-tree/{projcode}{query}')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_inactive_project_renders_its_allocation_for_a_date_inside_the_window(
        auth_client, inactive_project_with_allocation):
    project, alloc, resource_name = inactive_project_with_allocation
    inside = (alloc.start_date + timedelta(days=1)).strftime('%Y-%m-%d')
    html = _tree_fragment(auth_client, project.projcode, f'?active_at={inside}')
    assert resource_name in html
    assert 'No allocations found' not in html


def test_inactive_project_renders_today_without_error(auth_client, inactive_project_with_allocation):
    project, _, _ = inactive_project_with_allocation
    _tree_fragment(auth_client, project.projcode)


def test_deep_link_on_inactive_project_lands_on_allocations(auth_client, inactive_project_with_allocation):
    project, alloc, _ = inactive_project_with_allocation
    inside = (alloc.start_date + timedelta(days=1)).strftime('%Y-%m-%d')
    resp = auth_client.get(f'/admin/project/{project.projcode}/edit?active_at={inside}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="tab-pane fade show active" id="tab-allocations"' in html
    assert inside in html
