"""Tests for User.former_projects() and its user-card rendering gate.

Model-layer tests build a fresh membership graph with factories (Layer 2).
The HTTP tests follow the house convention (auth/render smoke only) and
rely solely on committed snapshot rows — Flask routes read a separate
db.session connection and cannot see factory rows (see
tests/unit/test_htmx_search_active_toggle.py).
"""
import pytest
from datetime import datetime, timedelta

from sam.manage import add_user_to_project

from factories import make_account, make_project, make_user

pytestmark = pytest.mark.unit

SECTION_LABEL = 'Inactive / Former Projects'


def _project_with_account(session, **project_kwargs):
    project = make_project(session, **project_kwargs)
    make_account(session, project=project)
    return project


class TestFormerProjects:
    """User.former_projects() bucket semantics."""

    def test_current_membership_is_not_former(self, session):
        user = make_user(session)
        project = _project_with_account(session)
        add_user_to_project(session, project.project_id, user.user_id)

        former = user.former_projects()
        assert former == {'inactive': [], 'ended': []}
        assert project in user.active_projects()

    def test_ended_membership_on_active_project(self, session):
        user = make_user(session)
        project = _project_with_account(session)
        add_user_to_project(
            session, project.project_id, user.user_id,
            start_date=datetime.now() - timedelta(days=400),
            end_date=datetime.now() - timedelta(days=30),
        )

        former = user.former_projects()
        assert former['ended'] == [project]
        assert former['inactive'] == []
        assert project not in user.active_projects()

    def test_membership_on_inactive_project(self, session):
        user = make_user(session)
        project = _project_with_account(session, active=False)
        add_user_to_project(session, project.project_id, user.user_id)

        former = user.former_projects()
        assert former['inactive'] == [project]
        assert former['ended'] == []

    def test_led_inactive_project_without_account_rows(self, session):
        """A lead with no AccountUser rows still sees the inactive project
        (covers the User.all_projects gap for accountless projects)."""
        user = make_user(session)
        project = make_project(session, lead=user, active=False)

        former = user.former_projects()
        assert former['inactive'] == [project]
        assert former['ended'] == []

    def test_buckets_sorted_by_projcode_and_disjoint(self, session):
        user = make_user(session)
        active_proj = _project_with_account(session)
        add_user_to_project(session, active_proj.project_id, user.user_id)

        inactive = [_project_with_account(session, active=False) for _ in range(2)]
        for p in inactive:
            add_user_to_project(session, p.project_id, user.user_id)

        ended = [_project_with_account(session) for _ in range(2)]
        for p in ended:
            add_user_to_project(
                session, p.project_id, user.user_id,
                start_date=datetime.now() - timedelta(days=400),
                end_date=datetime.now() - timedelta(days=30),
            )

        former = user.former_projects()
        assert former['inactive'] == sorted(inactive, key=lambda p: p.projcode)
        assert former['ended'] == sorted(ended, key=lambda p: p.projcode)
        assert active_proj not in former['inactive'] + former['ended']

    def test_as_of_reclassifies_ended_membership(self, session):
        """Before the membership's end_date the project is active, not former."""
        user = make_user(session)
        project = _project_with_account(session)
        end = datetime.now() - timedelta(days=30)
        add_user_to_project(
            session, project.project_id, user.user_id,
            start_date=datetime.now() - timedelta(days=400),
            end_date=end,
        )

        as_of = end - timedelta(days=1)
        former = user.former_projects(as_of=as_of)
        assert former == {'inactive': [], 'ended': []}
        assert project in user.active_projects(as_of=as_of)


class TestUserCardFormerProjectsGate:
    """Render/authz smoke for the user-card section (snapshot rows only)."""

    def test_admin_user_card_renders(self, auth_client):
        """benkirk (full perms) gets the card; section presence depends on
        snapshot data, so assert only a clean render."""
        resp = auth_client.get('/admin/user/benkirk')
        assert resp.status_code == 200
        assert b'Active Projects' in resp.data

    def test_own_info_page_omits_section(self, auth_client):
        """/user/info renders the shared macro with is_admin=False — the
        operator-only section must not appear even for a full-perm user."""
        resp = auth_client.get('/user/info')
        assert resp.status_code == 200
        assert SECTION_LABEL.encode() not in resp.data
