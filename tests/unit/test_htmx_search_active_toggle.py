"""The "Active … only" checkbox on the admin search boxes.

htmx omits an unchecked checkbox from the request entirely, so every
search endpoint behind one of these boxes must read an *absent*
``active_only`` as OFF — i.e. include inactive rows. Reading an absent
value as ON makes the checkbox inert: unchecking it changed nothing and
inactive users/projects stayed invisible in the UI even though
``sam-search --inactive-users`` found them.

The FK/member user pickers never send the param and must stay
active-only, so the users endpoint keys its default off ``context``.

These are HTTP-layer tests, so they read snapshot rows rather than
factory-built ones: Flask routes go through Flask-SQLAlchemy's
``db.session``, which has its own connection and cannot see uncommitted
rows written on the raw test ``session`` (see ``non_admin_client`` in
conftest for the same constraint).
"""
import pytest

pytestmark = pytest.mark.unit


class TestReadActiveOnlyHelper:
    """`read_active_only` is the single parser every route shares — it exists
    so the `'1'` vs `'true'` spelling split can't silently reappear.
    """

    @pytest.mark.parametrize('raw', ['1', 'true', 'True', 'TRUE', 'on', 'yes', ' 1 '])
    def test_truthy_spellings(self, raw):
        from webapp.utils.htmx import read_active_only
        assert read_active_only({'active_only': raw}) is True

    @pytest.mark.parametrize('raw', ['0', 'false', 'off', 'no', ''])
    def test_falsy_spellings(self, raw):
        from webapp.utils.htmx import read_active_only
        assert read_active_only({'active_only': raw}) is False

    def test_absent_is_off(self):
        """The core invariant: htmx drops unchecked checkboxes entirely."""
        from webapp.utils.htmx import read_active_only
        assert read_active_only({}) is False

    def test_absent_honors_explicit_default(self):
        from webapp.utils.htmx import read_active_only
        assert read_active_only({}, default=True) is True

    def test_explicit_value_beats_default(self):
        from webapp.utils.htmx import read_active_only
        assert read_active_only({'active_only': '0'}, default=True) is False


USERS_URL = '/admin/htmx/search/users'
PROJECTS_URL = '/admin/htmx/search-projects'
GROUPS_URL = '/admin/htmx/search/groups'


def _any_inactive(session, model, order_col):
    """First inactive snapshot row of `model`, or skip if the snapshot has none."""
    row = session.query(model).filter(~model.is_active).order_by(order_col).first()
    if row is None:
        pytest.skip(f'snapshot has no inactive {model.__name__} rows')
    return row


@pytest.fixture
def inactive_user(session):
    from sam import User
    return _any_inactive(session, User, User.user_id)


@pytest.fixture
def inactive_project(session):
    from sam import Project
    return _any_inactive(session, Project, Project.project_id)


@pytest.fixture
def inactive_group(session):
    from sam.core.groups import AdhocGroup
    return _any_inactive(session, AdhocGroup, AdhocGroup.group_name)


class TestUserSearchActiveToggle:

    def test_inactive_user_found_when_unchecked(self, auth_client, inactive_user):
        resp = auth_client.get(
            f'{USERS_URL}?q={inactive_user.username}&context=impersonate')
        assert resp.status_code == 200
        assert inactive_user.username in resp.get_data(as_text=True)

    def test_inactive_user_hidden_when_checked(self, auth_client, inactive_user):
        resp = auth_client.get(
            f'{USERS_URL}?q={inactive_user.username}'
            f'&context=impersonate&active_only=true')
        assert inactive_user.username not in resp.get_data(as_text=True)

    def test_active_user_found_either_way(self, auth_client):
        """`benkirk` is preserved verbatim by the obfuscated snapshot."""
        for suffix in ('', '&active_only=true'):
            resp = auth_client.get(
                f'{USERS_URL}?q=benkirk&context=impersonate{suffix}')
            assert 'benkirk' in resp.get_data(as_text=True), suffix

    @pytest.mark.parametrize('context', ['fk', 'bogus'])
    def test_picker_contexts_stay_active_only(self, auth_client, inactive_user, context):
        """FK pickers have no checkbox — an absent param must NOT open them up."""
        resp = auth_client.get(
            f'{USERS_URL}?q={inactive_user.username}&context={context}')
        assert inactive_user.username not in resp.get_data(as_text=True)


class TestProjectSearchActiveToggle:

    def test_inactive_project_found_when_unchecked(self, auth_client, inactive_project):
        resp = auth_client.get(f'{PROJECTS_URL}?q={inactive_project.projcode}')
        assert resp.status_code == 200
        assert inactive_project.projcode in resp.get_data(as_text=True)

    def test_inactive_project_hidden_when_checked(self, auth_client, inactive_project):
        resp = auth_client.get(
            f'{PROJECTS_URL}?q={inactive_project.projcode}&active_only=true')
        assert inactive_project.projcode not in resp.get_data(as_text=True)


class TestGroupSearchActiveToggle:

    def test_inactive_group_found_when_unchecked(self, auth_client, inactive_group):
        resp = auth_client.get(f'{GROUPS_URL}?q={inactive_group.group_name}')
        assert resp.status_code == 200
        assert inactive_group.group_name in resp.get_data(as_text=True)

    def test_inactive_group_hidden_when_checked(self, auth_client, inactive_group):
        resp = auth_client.get(
            f'{GROUPS_URL}?q={inactive_group.group_name}&active_only=true')
        assert inactive_group.group_name not in resp.get_data(as_text=True)


class TestToggleRefiresSearch:
    """The checkbox itself must re-run the query — otherwise the server-side
    default is invisible until the user retypes the search term.
    """

    @pytest.mark.parametrize('page,toggle_id,results_id', [
        ('/admin/users-groups', 'activeUsersOnly', 'userSearchResults'),
        ('/admin/users-groups', 'activeGroupsOnly', 'groupSearchResults'),
        ('/admin/projects', 'activeProjectsOnly', 'projectSearchResults'),
    ])
    def test_checkbox_is_htmx_wired(self, auth_client, page, toggle_id, results_id):
        html = auth_client.get(page).get_data(as_text=True)
        start = html.index(f'id="{toggle_id}"')
        block = html[start:start + 600]
        assert 'hx-get=' in block
        assert 'hx-trigger="change"' in block
        assert f'hx-target="#{results_id}"' in block
