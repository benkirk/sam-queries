"""Admin -> Events: the cross-project event page.

House rule: HTTP tests cover auth, validation, 404 and render smoke; the
lifecycle writes are covered at the model layer. Route handlers use
Flask-SQLAlchemy's own session, so the row they act on is committed here.
"""

import uuid
from datetime import date, timedelta

import pytest

from webapp.utils.rbac import Permission


PAGE = '/admin/events'
FRAGMENT = '/admin/events/fragment'
CODE = 'ZZ-ADMIN-EVENTS-TEST'


@pytest.fixture
def committed_event(app):
    """``(code, projcode)`` of a committed listed event. The code is unique per
    test: xdist workers share one database and this row is really committed."""
    from sam.core.account_requests import AccountRequestEvent
    from sam.projects.projects import Project
    from webapp.extensions import db

    code = f'ZZ-{uuid.uuid4().hex[:12].upper()}'
    with app.app_context():
        project = db.session.query(Project).filter(Project.is_active).first()
        AccountRequestEvent.create(
            db.session, event_code=code, name='Admin events test',
            project_id=project.project_id, created_by='benkirk', listed=True,
            accounts_needed_by=date.today() + timedelta(days=30))
        db.session.commit()
        projcode = project.projcode

    yield code, projcode

    with app.app_context():
        db.session.query(AccountRequestEvent).filter_by(event_code=code).delete()
        db.session.commit()


@pytest.fixture
def no_events_client(auth_client, monkeypatch):
    """`benkirk` without MANAGE_EVENTS (or SYSTEM_ADMIN), but still holding
    MANAGE_ACCOUNT_REQUESTS: the two permissions are separate."""
    from webapp.utils import rbac

    real = rbac.get_user_permissions
    monkeypatch.setattr(
        rbac, 'get_user_permissions',
        lambda user, *a, **k: {p for p in real(user, *a, **k)
                               if p not in (Permission.MANAGE_EVENTS,
                                            Permission.SYSTEM_ADMIN)})
    return auth_client


GETS = [PAGE, FRAGMENT, '/admin/htmx/events/new-form', f'/admin/events/{CODE}/edit-form',
        '/admin/htmx/events/project-search?q=SC', f'/admin/htmx/events/{CODE}/enrollees']
ROSTER_FORM = '/admin/htmx/events/{}/roster-form'
ROSTER_POST = '/admin/htmx/events/{}/roster'
POSTS = ['/admin/htmx/events/new', f'/admin/events/{CODE}', f'/admin/events/{CODE}/close',
         f'/admin/events/{CODE}/reopen']


def _strip(monkeypatch, *permissions):
    from webapp.utils import rbac
    real = rbac.get_user_permissions
    monkeypatch.setattr(
        rbac, 'get_user_permissions',
        lambda user, *a, **k: {p for p in real(user, *a, **k) if p not in permissions})


class TestThePermissionBoundary:

    def test_a_roster_is_an_account_request_write(self, auth_client, monkeypatch,
                                                  committed_event):
        """MANAGE_EVENTS alone lists enrollees but may not paste a roster."""
        code, _ = committed_event
        _strip(monkeypatch, Permission.MANAGE_ACCOUNT_REQUESTS, Permission.SYSTEM_ADMIN)
        assert auth_client.get(f'/admin/htmx/events/{code}/enrollees').status_code == 200
        assert auth_client.get(ROSTER_FORM.format(code)).status_code == 403
        assert auth_client.post(ROSTER_POST.format(code), data={}).status_code == 403
        assert 'Paste a roster' not in auth_client.get(FRAGMENT).get_data(as_text=True)

    @pytest.mark.parametrize('path', GETS)
    def test_anonymous_is_refused(self, client, path):
        assert client.get(path).status_code in (302, 401, 403)

    @pytest.mark.parametrize('path', GETS)
    def test_without_the_permission_is_403(self, no_events_client, path):
        assert no_events_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', POSTS)
    def test_without_the_permission_cannot_post(self, no_events_client, path):
        assert no_events_client.post(path, data={}).status_code == 403

    def test_a_projectless_sponsor_search_needs_the_permission(self, no_events_client):
        resp = no_events_client.get('/admin/htmx/search/users?context=sponsor&q=ben')
        assert resp.status_code == 400

    def test_a_projectless_sponsor_search_works_for_the_holder(self, auth_client):
        resp = auth_client.get('/admin/htmx/search/users?context=sponsor&q=ben')
        assert resp.status_code == 200

    def test_the_tab_and_nav_both_list_it(self, auth_client):
        body = auth_client.get('/admin/contracts').get_data(as_text=True)
        assert body.count('href="/admin/events"') >= 2

    def test_both_are_hidden_without_the_permission(self, no_events_client):
        page = no_events_client.get('/admin/contracts')
        assert page.status_code == 200
        assert b'href="/admin/events"' not in page.data
        assert b'href="/admin/account-requests"' in page.data


class TestRenderSmoke:

    def test_the_page_ships_the_switch_and_the_modal_shell(self, auth_client):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'id="eventsCardActiveOnly"' in html
        assert 'id="invitationModal"' in html

    def test_the_card_lists_an_event_with_its_project_and_flags(self, auth_client,
                                                                committed_event):
        code, projcode = committed_event
        html = auth_client.get(FRAGMENT + '?active_only=1').get_data(as_text=True)
        assert code in html and projcode in html
        assert 'Listed' in html
        assert f'/register/{code}' in html

    def test_enrollees_load_on_demand_not_per_row(self, auth_client, committed_event):
        code, _ = committed_event
        card = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert f'/admin/htmx/events/{code}/enrollees' in card
        assert 'shown.bs.collapse once' in card
        body = auth_client.get(f'/admin/htmx/events/{code}/enrollees').get_data(as_text=True)
        assert 'No one has enrolled' in body

    def test_the_roster_form_posts_back_to_the_admin_route(self, auth_client, committed_event):
        code, projcode = committed_event
        html = auth_client.get(ROSTER_FORM.format(code)).get_data(as_text=True)
        assert ROSTER_POST.format(code) in html and projcode in html
        assert 'data-bs-toggle="modal"' not in html

    def test_an_empty_roster_re_renders_the_form(self, auth_client, committed_event):
        resp = auth_client.post(ROSTER_POST.format(committed_event[0]), data={'roster': '# nobody'})
        assert 'No people found' in resp.get_data(as_text=True)

    def test_the_create_form_has_a_project_picker_and_the_listed_box(self, auth_client):
        html = auth_client.get('/admin/htmx/events/new-form').get_data(as_text=True)
        assert 'name="project_id"' in html
        assert 'name="listed"' in html

    def test_the_edit_form_checks_listed_and_has_no_project_picker(self, auth_client,
                                                                   committed_event):
        html = auth_client.get(f'/admin/events/{committed_event[0]}/edit-form').get_data(as_text=True)
        assert 'name="project_id"' not in html
        assert 'name="listed"' in html and 'checked' in html

    def test_an_unknown_code_is_404(self, auth_client):
        assert auth_client.get('/admin/events/NO-SUCH-EVENT/edit-form').status_code == 404
        assert auth_client.post('/admin/events/NO-SUCH-EVENT/close').status_code == 404


class TestCreateValidation:

    def test_a_missing_project_is_a_field_error(self, auth_client):
        resp = auth_client.post('/admin/htmx/events/new', data={
            'event_code': 'ZZ-NO-PROJECT', 'name': 'x',
            'accounts_needed_by': '2030-01-01'})
        assert 'Pick a project.' in resp.get_data(as_text=True)

    def test_a_duplicate_code_is_refused(self, auth_client, app, committed_event):
        from sam.projects.projects import Project
        from webapp.extensions import db
        code, projcode = committed_event
        with app.app_context():
            pid = Project.get_by_projcode(db.session, projcode).project_id
        resp = auth_client.post('/admin/htmx/events/new', data={
            'event_code': code, 'name': 'dup', 'project_id': pid,
            'accounts_needed_by': '2030-01-01'})
        assert 'already in use' in resp.get_data(as_text=True)

    def test_an_inactive_project_is_refused(self, auth_client, app):
        from sam.projects.projects import Project
        from webapp.extensions import db
        with app.app_context():
            project = db.session.query(Project).filter(~Project.is_active).first()
            pid, projcode = project.project_id, project.projcode
        resp = auth_client.post('/admin/htmx/events/new', data={
            'event_code': f'ZZ-{uuid.uuid4().hex[:12].upper()}', 'name': 'x',
            'project_id': pid, 'accounts_needed_by': '2030-01-01'})
        assert f'{projcode} is not an active project' in resp.get_data(as_text=True)


class TestTheListedSentinel:
    """An unchecked box posts nothing, so its own key cannot say "leave it alone"."""

    def _listed(self, app, code):
        from sam.core.account_requests import AccountRequestEvent
        from webapp.extensions import db
        with app.app_context():
            return db.session.query(AccountRequestEvent).filter_by(event_code=code).one().listed

    def test_an_edit_that_never_drew_the_box_keeps_it(self, auth_client, app, committed_event):
        code, _ = committed_event
        resp = auth_client.put(f'/admin/events/{code}', data={'name': 'Renamed'})
        assert resp.status_code == 200
        assert self._listed(app, code) is True

    def test_the_drawn_box_left_unchecked_clears_it(self, auth_client, app, committed_event):
        code, _ = committed_event
        html = auth_client.get(f'/admin/events/{code}/edit-form').get_data(as_text=True)
        assert 'name="listed_present"' in html
        auth_client.put(f'/admin/events/{code}', data={'name': 'Renamed', 'listed_present': '1'})
        assert self._listed(app, code) is False


class TestFacilityScope:
    """A holder scoped to one facility sees and acts on that facility's events only."""

    @pytest.fixture
    def scoped_to(self, monkeypatch):
        def _scope(facility):
            from webapp.utils import rbac
            _strip(monkeypatch, Permission.MANAGE_EVENTS, Permission.SYSTEM_ADMIN)
            monkeypatch.setitem(rbac.USER_FACILITY_PERMISSIONS, 'benkirk',
                                {facility: {Permission.MANAGE_EVENTS}})
        return _scope

    @pytest.fixture
    def event_facility(self, app, committed_event):
        from sam.projects.projects import Project
        from webapp.extensions import db
        with app.app_context():
            return Project.get_by_projcode(db.session, committed_event[1]).facility_name

    def test_in_scope_is_listed_and_editable(self, auth_client, scoped_to, committed_event,
                                             event_facility):
        if event_facility is None:
            pytest.skip('the snapshot project has no facility chain')
        scoped_to(event_facility)
        code, _ = committed_event
        assert code in auth_client.get(FRAGMENT).get_data(as_text=True)
        assert auth_client.get(f'/admin/events/{code}/edit-form').status_code == 200

    def test_out_of_scope_is_hidden_and_403(self, auth_client, scoped_to, committed_event):
        scoped_to('NO-SUCH-FACILITY')
        code, _ = committed_event
        assert auth_client.get(PAGE).status_code == 200
        assert code not in auth_client.get(FRAGMENT).get_data(as_text=True)
        assert auth_client.get(f'/admin/events/{code}/edit-form').status_code == 403
        assert auth_client.post(f'/admin/events/{code}/close').status_code == 403
        assert auth_client.get(f'/admin/htmx/events/{code}/enrollees').status_code == 403

    def test_out_of_scope_projects_are_not_offered_or_accepted(self, auth_client, app, scoped_to,
                                                               committed_event):
        from sam.projects.projects import Project
        from webapp.extensions import db
        scoped_to('NO-SUCH-FACILITY')
        _, projcode = committed_event
        with app.app_context():
            pid = Project.get_by_projcode(db.session, projcode).project_id
        search = auth_client.get(f'/admin/htmx/events/project-search?q={projcode}')
        assert projcode not in search.get_data(as_text=True)
        resp = auth_client.post('/admin/htmx/events/new', data={
            'event_code': f'ZZ-{uuid.uuid4().hex[:12].upper()}', 'name': 'x',
            'project_id': pid, 'accounts_needed_by': '2030-01-01'})
        assert 'outside your facilities' in resp.get_data(as_text=True)
