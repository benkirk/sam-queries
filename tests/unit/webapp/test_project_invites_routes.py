"""The Invitations tab routes on the project_members blueprint.

House rule: HTTP tests cover auth, validation, 404 and render smoke; the
writes (invite, roster, event) are covered in `test_account_requests_manage.py`
and `test_account_requests_model.py`. Route handlers use Flask-SQLAlchemy's
own session, so factory rows are invisible to them.
"""

import pytest

from webapp.utils.rbac import Permission

pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def invitations_disabled_app(test_db_url, status_db_url):
    """The prod posture: the flag off means the blueprint is never mounted."""
    from webapp.run import create_app
    return create_app(config_overrides={
        'SQLALCHEMY_DATABASE_URI': test_db_url,
        'SQLALCHEMY_BINDS': {'system_status': status_db_url},
        'ACCOUNT_INVITATIONS_ENABLED': False,
    })


@pytest.fixture
def snapshot_projcode(session):
    """A committed, active project the route handlers can see."""
    from sam.projects.projects import Project
    projcode = (session.query(Project.projcode).filter(Project.is_active)
                .order_by(Project.project_id).limit(1).scalar())
    assert projcode, 'snapshot has no active projects'
    return projcode


@pytest.fixture
def steward_less_client(auth_client, monkeypatch):
    """`benkirk` without MANAGE_ACCOUNT_REQUESTS, MANAGE_EVENTS or SYSTEM_ADMIN:
    only a project he leads or administers lets him in."""
    from webapp.utils import rbac
    real = rbac.get_user_permissions
    monkeypatch.setattr(
        rbac, 'get_user_permissions',
        lambda user, *a, **k: {p for p in real(user, *a, **k)
                               if p not in (Permission.MANAGE_ACCOUNT_REQUESTS,
                                            Permission.MANAGE_EVENTS,
                                            Permission.SYSTEM_ADMIN)})
    return auth_client


@pytest.fixture
def operator_client(auth_client, monkeypatch):
    """`benkirk` WITH MANAGE_ACCOUNT_REQUESTS and MANAGE_EVENTS -- a real operator, whatever
    the snapshot grants him. The snapshot makes him a project lead, not an
    operator, so the create-event (operator-only) path needs this."""
    from webapp.utils import rbac
    real = rbac.get_user_permissions
    monkeypatch.setattr(
        rbac, 'get_user_permissions',
        lambda user, *a, **k: real(user, *a, **k) | {Permission.MANAGE_ACCOUNT_REQUESTS,
                                                     Permission.MANAGE_EVENTS})
    return auth_client


@pytest.fixture
def unled_projcode(session):
    """A snapshot project benkirk neither leads nor administers."""
    from sam.core.users import User
    from sam.projects.projects import Project
    me = User.get_by_username(session, 'benkirk')
    project = (session.query(Project)
               .filter(Project.project_lead_user_id != me.user_id)
               .filter((Project.project_admin_user_id.is_(None))
                       | (Project.project_admin_user_id != me.user_id))
               .filter(Project.parent_id.is_(None))
               .first())
    assert project is not None
    return project.projcode


@pytest.fixture
def led_projcode(session):
    """A snapshot project benkirk leads."""
    from sam.core.users import User
    from sam.projects.projects import Project
    me = User.get_by_username(session, 'benkirk')
    projcode = (session.query(Project.projcode)
                .filter(Project.project_lead_user_id == me.user_id, Project.is_active)
                .order_by(Project.project_id).limit(1).scalar())
    assert projcode, 'benkirk leads no active snapshot project'
    return projcode


class TestThePermissionBoundary:

    def test_anonymous_is_refused(self, client, snapshot_projcode):
        resp = client.get(f'/project-invitations/{snapshot_projcode}/invitations')
        assert resp.status_code in (302, 401, 403)
        resp = client.get('/project-invitations/institutions?organization=universit')
        assert resp.status_code in (302, 401, 403), 'the suggestions need a login'

    def test_a_non_steward_without_the_permission_is_403(self, steward_less_client,
                                                         unled_projcode):
        for path in (f'/project-invitations/{unled_projcode}/invitations',
                     f'/project-invitations/{unled_projcode}/invite-form',
                     f'/project-invitations/{unled_projcode}/events/new-form'):
            assert steward_less_client.get(path).status_code == 403, path
        assert steward_less_client.post(
            f'/project-invitations/{unled_projcode}/invite', data={}).status_code == 403

    def test_the_sponsor_search_needs_a_login(self, client, snapshot_projcode):
        resp = client.get(f'/admin/htmx/search/users?context=sponsor&q=user'
                          f'&projcode={snapshot_projcode}')
        assert resp.status_code in (302, 401)

    def test_the_sponsor_search_is_gated_like_the_event_routes(
            self, steward_less_client, led_projcode, unled_projcode):
        url = '/admin/htmx/search/users?context=sponsor&q=user'
        assert steward_less_client.get(url).status_code == 400, 'projcode is required'
        assert steward_less_client.get(f'{url}&projcode={unled_projcode}').status_code == 403
        resp = steward_less_client.get(f'{url}&projcode={led_projcode}')
        assert resp.status_code == 200, 'a lead without VIEW_USERS may pick a sponsor'
        assert 'fk-search-result' in resp.get_data(as_text=True)

    def test_an_unknown_event_code_is_404(self, auth_client):
        for path in ('/project-invitations/events/NO-SUCH-EVENT/roster-form',
                     '/project-invitations/events/NO-SUCH-EVENT/edit-form'):
            assert auth_client.get(path).status_code == 404, path
        assert auth_client.post('/project-invitations/events/NO-SUCH-EVENT/roster',
                                data={'roster': 'A B <a@b.edu>'}).status_code == 404

    def test_the_tab_is_drawn_for_the_holder(self, auth_client, snapshot_projcode):
        page = auth_client.get(f'/admin/project/{snapshot_projcode}/edit?tab=invitations')
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'id="invitations-tab"' in html
        assert 'id="invitationModal"' in html


class TestRenderSmoke:

    def test_the_tab_fragment_renders(self, auth_client, snapshot_projcode):
        resp = auth_client.get(f'/project-invitations/{snapshot_projcode}/invitations')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Invite a person' in html and 'New event' in html

    def test_the_forms_render(self, auth_client, snapshot_projcode):
        for path in (f'/project-invitations/{snapshot_projcode}/invite-form',
                     f'/project-invitations/{snapshot_projcode}/events/new-form'):
            resp = auth_client.get(path)
            assert resp.status_code == 200, path
            assert 'invitationModalLabel' in resp.get_data(as_text=True)
        assert 'name="instructions"' in resp.get_data(as_text=True), \
            'the event form carries the participant-facing instructions'

    def test_the_event_form_picks_the_sponsor_from_the_user_search(self, auth_client,
                                                                   snapshot_projcode):
        html = auth_client.get(f'/project-invitations/{snapshot_projcode}/events/new-form'
                               ).get_data(as_text=True)
        assert 'fk-picker' in html and 'name="extra_sponsor_user_id"' in html
        assert (f'/admin/htmx/search/users?context=sponsor&amp;projcode={snapshot_projcode}'
                in html)
        assert 'extra_sponsor_username' not in html

    def test_the_invite_form_suggests_institutions(self, auth_client, snapshot_projcode):
        html = auth_client.get(f'/project-invitations/{snapshot_projcode}/invite-form').get_data(as_text=True)
        assert 'list="organization-list"' in html and 'hx-get="/project-invitations/institutions"' in html
        assert 'Institution' in html and 'Organization' not in html
        body = auth_client.get('/project-invitations/institutions?organization=universit').get_data(as_text=True)
        assert 0 < body.count('<option value="') <= 10


class TestValidation:

    def test_an_invite_without_a_name_re_renders_with_errors(self, auth_client,
                                                            snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/invite',
                                data={'email': 'x@example.edu'})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'first_name' in html and 'HX-Trigger' not in resp.headers

    def test_an_invite_with_a_bad_email_is_refused(self, auth_client, snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/invite',
                                data={'email': 'nope', 'first_name': 'A', 'last_name': 'B'})
        assert resp.status_code == 200 and 'HX-Trigger' not in resp.headers

    def test_an_invite_naming_a_foreign_event_is_refused(self, auth_client,
                                                        snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/invite',
                                data={'email': 'a@example.edu', 'first_name': 'A',
                                      'last_name': 'B', 'event_code': 'NOT-HERE'})
        assert resp.status_code == 200
        assert 'not an event on' in resp.get_data(as_text=True)
        assert 'HX-Trigger' not in resp.headers

    def test_a_bad_event_code_is_refused(self, auth_client, snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/events',
                                data={'event_code': 'has space', 'name': 'x',
                                      'accounts_needed_by': '2026-10-05'})
        assert resp.status_code == 200
        assert 'letters, digits or dashes' in resp.get_data(as_text=True)
        assert 'HX-Trigger' not in resp.headers

    def test_an_unknown_sponsor_id_is_refused(self, auth_client, snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/events',
                                data={'event_code': 'SPN-2026', 'name': 'x',
                                      'accounts_needed_by': '2026-10-05',
                                      'extra_sponsor_user_id': '999999999'})
        assert resp.status_code == 200
        assert 'not an active SAM user' in resp.get_data(as_text=True)
        assert 'HX-Trigger' not in resp.headers

    def test_a_backwards_window_is_refused(self, auth_client, snapshot_projcode):
        resp = auth_client.post(f'/project-invitations/{snapshot_projcode}/events',
                                data={'event_code': 'WIN-2026', 'name': 'x',
                                      'accounts_needed_by': '2026-10-05',
                                      'opens_at': '2026-10-02T09:00',
                                      'closes_at': '2026-10-01T09:00'})
        assert resp.status_code == 200
        assert 'close after it opens' in resp.get_data(as_text=True)
        assert 'HX-Trigger' not in resp.headers


class TestTheInvitationsFlag:
    """ACCOUNT_INVITATIONS_ENABLED gates the whole tab: off in prod so the
    initial capability is the XRAS-mirrored Accounts queue only. The queue and
    the XRAS Pending-Users card are not behind it."""

    def test_the_class_defaults(self):
        import os
        from webapp.config import DevelopmentConfig, ProductionConfig, TestingConfig
        if 'ACCOUNT_INVITATIONS_ENABLED' not in os.environ:
            assert ProductionConfig.ACCOUNT_INVITATIONS_ENABLED is False
            assert DevelopmentConfig.ACCOUNT_INVITATIONS_ENABLED is True
            assert TestingConfig.ACCOUNT_INVITATIONS_ENABLED is True

    def test_on_by_default_outside_production(self, app):
        assert app.config['ACCOUNT_INVITATIONS_ENABLED'] is True
        assert 'project_invites' in app.blueprints

    def test_off_means_404_and_no_blueprint(self, invitations_disabled_app,
                                            snapshot_projcode):
        client = invitations_disabled_app.test_client()
        assert 'project_invites' not in invitations_disabled_app.blueprints
        for path in (f'/project-invitations/{snapshot_projcode}/invitations',
                     f'/project-invitations/{snapshot_projcode}/invite-form',
                     f'/project-invitations/{snapshot_projcode}/events/new-form',
                     '/project-invitations/institutions?organization=univ'):
            assert client.get(path).status_code == 404, path
        assert client.post(
            f'/project-invitations/{snapshot_projcode}/invite', data={}).status_code == 404
        # The members blueprint on the same prefix is untouched.
        assert client.get(f'/project-members/{snapshot_projcode}').status_code != 404

    def test_off_hides_the_tab_and_its_modal(self, auth_client, app, monkeypatch,
                                            snapshot_projcode):
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', False)
        html = auth_client.get(
            f'/admin/project/{snapshot_projcode}/edit').get_data(as_text=True)
        assert 'id="invitations-tab"' not in html
        assert 'id="invitationModal"' not in html


class TestEventCreationIsOperatorOnly:
    """A PI/admin (project steward) may invite people and manage an event that
    exists, but may NOT create one -- minting an event code is operator-only
    (MANAGE_ACCOUNT_REQUESTS for the facility), so a PI cannot surprise the
    operators with a code."""

    def test_a_lead_may_invite_but_not_create_an_event(self, steward_less_client,
                                                       led_projcode):
        # A project he leads: steward access lets him invite ...
        assert steward_less_client.get(
            f'/project-invitations/{led_projcode}/invite-form').status_code == 200
        # ... but creating an event needs the operator permission he lacks.
        assert steward_less_client.get(
            f'/project-invitations/{led_projcode}/events/new-form').status_code == 403
        assert steward_less_client.post(
            f'/project-invitations/{led_projcode}/events', data={}).status_code == 403

    def test_a_lead_sees_invite_but_not_the_new_event_button(self, steward_less_client,
                                                             led_projcode):
        lead_html = steward_less_client.get(
            f'/project-invitations/{led_projcode}/invitations').get_data(as_text=True)
        assert 'Invite a person' in lead_html, 'a lead still invites'
        assert 'New event' not in lead_html, 'a lead cannot open an event code'

    def test_an_operator_gets_the_new_event_button_and_form(self, operator_client,
                                                            led_projcode):
        op_html = operator_client.get(
            f'/project-invitations/{led_projcode}/invitations').get_data(as_text=True)
        assert 'New event' in op_html, 'an operator opens events'
        assert operator_client.get(
            f'/project-invitations/{led_projcode}/events/new-form').status_code == 200
