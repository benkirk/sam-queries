"""The User / Resource Access grid always shows the lead and admin, with their real state."""
import re

import pytest

from sam.accounting.accounts import AccountUser

from factories import make_account, make_allocation, make_project, make_user

GRID = 'dashboards/admin/fragments/project_access_grid_htmx.html'


@pytest.fixture
def render(app):
    from flask import render_template

    def _render(project):
        status = project.get_members_access_status(active_only=True)
        with app.test_request_context():
            return render_template(
                GRID, project=project, projcode=project.projcode,
                columns=status['columns'], member_rows=status['members'],
                active_only=True, can_view_users=True, errors=[])
    return _render


def _row_html(html, username):
    return next(tr for tr in html.split('<tr>') if f"({username})" in tr)


def test_rowless_lead_renders_with_grantable_cells(session, render):
    project = make_project(session)
    for _ in range(2):
        make_allocation(session, account=make_account(session, project=project))
    session.query(AccountUser).filter_by(user_id=project.project_lead_user_id).delete()
    session.flush()
    session.expire_all()

    html = render(project)
    row = _row_html(html, project.lead.username)
    assert '>lead<' in row
    boxes = re.findall(r'<input type="checkbox".*?>', row, re.S)
    assert len(boxes) == 2
    assert not any(re.search(r'\schecked\b', b) for b in boxes)
    assert not any('disabled' in b for b in boxes)
    assert all('hx-post' in b for b in boxes)
    assert 'Reconcile all' in html


def test_live_admin_cells_are_locked(session, render):
    project = make_project(session)
    make_allocation(session, account=make_account(session, project=project))
    admin = make_user(session)
    project.update(project_admin_user_id=admin.user_id)
    session.expire_all()

    row = _row_html(render(project), admin.username)
    assert '>admin<' in row
    box = re.search(r'<input type="checkbox".*?>', row, re.S).group(0)
    assert 'checked' in box and 'disabled' in box
    assert 'project admin cannot be revoked' in box
