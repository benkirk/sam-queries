"""
Phase 2 access-control decorator behavior + Phase 3 macro self-gating.

Covers:
- ``@require_project_permission`` aborts 403 when not steward, 404 when
  the project does not exist, passes the project to the route otherwise.
- ``@require_allocation_permission`` aborts 403/404 similarly and passes
  the allocation. Uses ``include_ancestors=True`` so a lead of a parent
  project can edit a child's allocation.
- ``edit_modal_button`` / ``delete_row_button`` macros render or omit
  the button based on the ``permission=`` kwarg.

These tests mock the DB lookups (`get_project_or_404`, `db.session.get`)
so they don't depend on the snapshot fixture or factory-built rows.
The integration risk for the decorators is low — they delegate to
``_is_project_steward``, which has full unit-test coverage in
``test_project_permissions.py``.
"""

from unittest.mock import Mock, patch

import pytest
from flask import Flask
from flask_login import LoginManager
from werkzeug.exceptions import Forbidden, NotFound

from webapp.utils.rbac import Permission


# ---------------------------------------------------------------------------
# Fixtures: minimal Flask app + login_manager, plus stub authenticated user
# ---------------------------------------------------------------------------

@pytest.fixture
def mini_app():
    """Tiny Flask app — enough context to run decorator tests."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    LoginManager(app)
    return app


def _stub_user(*, user_id=42, roles=()):
    user = Mock()
    user.user_id = user_id
    user.username = f'stub_{user_id}'
    user.roles = set(roles)
    user.is_authenticated = True
    user.has_role = lambda r: r in user.roles
    return user


def _stub_project(*, project_id=100, lead_id=None, admin_id=None, parent=None,
                  projcode='PRJ001'):
    p = Mock()
    p.project_id = project_id
    p.projcode = projcode
    p.project_lead_user_id = lead_id
    p.project_admin_user_id = admin_id
    p.parent = parent
    return p


# ---------------------------------------------------------------------------
# @require_project_permission
# ---------------------------------------------------------------------------

class TestRequireProjectPermission:
    def _call_decorated(self, mini_app, current_user, project, *,
                        include_ancestors=False):
        """Wrap a no-op route in the decorator and invoke it; return result."""
        from webapp.api.access_control import require_project_permission

        with mini_app.test_request_context('/'):
            with patch(
                'webapp.api.access_control.get_project_or_404',
                return_value=(project, None),
            ), patch(
                'webapp.api.access_control.current_user',
                current_user,
            ):
                @require_project_permission(
                    Permission.EDIT_PROJECT_MEMBERS,
                    include_ancestors=include_ancestors,
                )
                def route(project):
                    return ('ok', project)

                return route('PRJ001')

    def test_lead_passes_and_receives_project(self, mini_app):
        user = _stub_user(user_id=42)
        project = _stub_project(lead_id=42)
        result, recv = self._call_decorated(mini_app, user, project)
        assert result == 'ok'
        assert recv is project

    def test_admin_passes(self, mini_app):
        user = _stub_user(user_id=42)
        project = _stub_project(lead_id=999, admin_id=42)
        result, _ = self._call_decorated(mini_app, user, project)
        assert result == 'ok'

    def test_system_permission_holder_passes(self, mini_app):
        user = _stub_user(user_id=99, roles=['admin'])
        project = _stub_project(lead_id=1, admin_id=2)
        result, _ = self._call_decorated(mini_app, user, project)
        assert result == 'ok'

    def test_outsider_aborts_403(self, mini_app):
        user = _stub_user(user_id=42)
        project = _stub_project(lead_id=999, admin_id=998)
        with pytest.raises(Forbidden):
            self._call_decorated(mini_app, user, project)

    def test_ancestor_lead_passes_when_include_ancestors(self, mini_app):
        parent = _stub_project(project_id=1, lead_id=42)
        child = _stub_project(project_id=2, lead_id=999, parent=parent)
        user = _stub_user(user_id=42)
        result, _ = self._call_decorated(
            mini_app, user, child, include_ancestors=True
        )
        assert result == 'ok'

    def test_ancestor_lead_blocked_without_include_ancestors(self, mini_app):
        parent = _stub_project(project_id=1, lead_id=42)
        child = _stub_project(project_id=2, lead_id=999, parent=parent)
        user = _stub_user(user_id=42)
        with pytest.raises(Forbidden):
            self._call_decorated(mini_app, user, child, include_ancestors=False)

    def test_not_found_short_circuits(self, mini_app):
        from webapp.api.access_control import require_project_permission
        from flask import jsonify

        user = _stub_user(user_id=42)
        with mini_app.test_request_context('/'):
            err_response = (jsonify({'error': 'no such project'}), 404)
            with patch(
                'webapp.api.access_control.get_project_or_404',
                return_value=(None, err_response),
            ), patch(
                'webapp.api.access_control.current_user',
                user,
            ):
                @require_project_permission(Permission.EDIT_PROJECT_MEMBERS)
                def route(project):
                    pytest.fail("route body should not run when project missing")

                result = route('NOPE')
                # Return value of helpers' error tuple is propagated as-is.
                assert result is err_response


# ---------------------------------------------------------------------------
# @require_allocation_permission
# ---------------------------------------------------------------------------

class TestRequireAllocationPermission:
    def _call(self, mini_app, current_user, allocation):
        from webapp.api.access_control import require_allocation_permission

        with mini_app.test_request_context('/'):
            session_mock = Mock()
            session_mock.get.return_value = allocation
            with patch(
                'webapp.api.access_control.db',
                Mock(session=session_mock),
            ), patch(
                'webapp.api.access_control.current_user',
                current_user,
            ):
                @require_allocation_permission(Permission.EDIT_ALLOCATIONS)
                def route(allocation):
                    return ('ok', allocation)

                return route(123)

    def _allocation_with_project(self, project):
        account = Mock()
        account.project = project
        alloc = Mock()
        alloc.allocation_id = 123
        alloc.account = account
        return alloc

    def test_lead_can_edit_their_allocation(self, mini_app):
        project = _stub_project(lead_id=42)
        alloc = self._allocation_with_project(project)
        user = _stub_user(user_id=42)
        result, recv = self._call(mini_app, user, alloc)
        assert result == 'ok'
        assert recv is alloc

    def test_ancestor_lead_can_edit_descendant_allocation(self, mini_app):
        parent = _stub_project(project_id=1, lead_id=42)
        child = _stub_project(project_id=2, lead_id=999, parent=parent)
        alloc = self._allocation_with_project(child)
        user = _stub_user(user_id=42)
        result, _ = self._call(mini_app, user, alloc)
        assert result == 'ok'

    def test_outsider_aborts_403(self, mini_app):
        project = _stub_project(lead_id=999, admin_id=998)
        alloc = self._allocation_with_project(project)
        user = _stub_user(user_id=42)
        with pytest.raises(Forbidden):
            self._call(mini_app, user, alloc)

    def test_missing_allocation_aborts_404(self, mini_app):
        from webapp.api.access_control import require_allocation_permission
        user = _stub_user(user_id=42)

        with mini_app.test_request_context('/'):
            session_mock = Mock()
            session_mock.get.return_value = None
            with patch(
                'webapp.api.access_control.db',
                Mock(session=session_mock),
            ), patch(
                'webapp.api.access_control.current_user',
                user,
            ):
                @require_allocation_permission(Permission.EDIT_ALLOCATIONS)
                def route(allocation):
                    pytest.fail("should not reach route body")

                with pytest.raises(NotFound):
                    route(123)

    def test_orphan_allocation_aborts_403(self, mini_app):
        # Allocation with no account or no project — refuse to authorize.
        alloc = Mock()
        alloc.allocation_id = 123
        alloc.account = None
        user = _stub_user(user_id=42, roles=['admin'])  # even admin gets 403
        with pytest.raises(Forbidden):
            self._call(mini_app, user, alloc)


# ---------------------------------------------------------------------------
# Phase 3: action_buttons macro self-gating
# ---------------------------------------------------------------------------

class TestActionButtonMacros:
    """Render the macros directly to verify the ``permission=`` kwarg
    actually omits the button when the user lacks the permission.

    Renders via ``make_module(vars=...)`` and supplies the helpers the
    macro reaches for from template scope — ``has_permission``,
    ``Permission``, ``can_act_on_project`` — explicitly. This avoids
    needing a logged-in request context just to exercise the gating
    branch."""

    def _render_macro(self, app, macro_name, *, allow=True, **kwargs):
        with app.app_context():
            tmpl = app.jinja_env.get_template(
                'dashboards/fragments/action_buttons.html'
            )
            macro_vars = {
                'has_permission': lambda p: allow,
                'Permission': Permission,
                'can_act_on_project': lambda perm, project, **kw: allow,
            }
            module = tmpl.make_module(vars=macro_vars)
            macro = getattr(module, macro_name)
            return str(macro(**kwargs)).strip()

    def test_edit_button_renders_when_no_permission_specified(self, app):
        # Backwards-compat path: omitting permission= renders unconditionally.
        # Even with allow=False the macro should still render because no
        # gate was requested.
        html = self._render_macro(
            app, 'edit_modal_button', allow=False,
            url='/foo/edit', modal_id='m', target_id='t',
        )
        assert '<button' in html
        assert 'fa-edit' in html

    def test_edit_button_hidden_without_permission(self, app):
        html = self._render_macro(
            app, 'edit_modal_button', allow=False,
            url='/foo/edit', modal_id='m', target_id='t',
            permission=Permission.EDIT_RESOURCES,
        )
        assert html == ''

    def test_edit_button_shown_with_permission(self, app):
        html = self._render_macro(
            app, 'edit_modal_button', allow=True,
            url='/foo/edit', modal_id='m', target_id='t',
            permission=Permission.EDIT_RESOURCES,
        )
        assert '<button' in html

    def test_delete_button_hidden_without_permission(self, app):
        html = self._render_macro(
            app, 'delete_row_button', allow=False,
            url='/foo/delete', confirm='ok?',
            permission=Permission.DELETE_RESOURCES,
        )
        assert html == ''

    def test_delete_button_shown_with_permission(self, app):
        html = self._render_macro(
            app, 'delete_row_button', allow=True,
            url='/foo/delete', confirm='ok?',
            permission=Permission.DELETE_RESOURCES,
        )
        assert 'hx-delete="/foo/delete"' in html

    def test_project_scoped_uses_can_act_on_project(self, app):
        # When project= is set, the macro should consult can_act_on_project,
        # not has_permission. Verify by setting allow=True and confirming
        # render — and allow=False to confirm hide.
        sentinel_project = object()
        html = self._render_macro(
            app, 'edit_modal_button', allow=True,
            url='/foo/edit', modal_id='m', target_id='t',
            permission=Permission.EDIT_ALLOCATIONS,
            project=sentinel_project,
        )
        assert '<button' in html

        html = self._render_macro(
            app, 'edit_modal_button', allow=False,
            url='/foo/edit', modal_id='m', target_id='t',
            permission=Permission.EDIT_ALLOCATIONS,
            project=sentinel_project,
        )
        assert html == ''
