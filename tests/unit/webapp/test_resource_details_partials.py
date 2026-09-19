"""Route tests for the lazy user-subtree / day-subtree fragments.

These complement the unit tests for the underlying summary queries
(:file:`test_resource_summary_queries.py`). They use snapshot data
(``active_project``) and monkeypatch the query functions so each
test pins exactly what the fragment receives, without depending on
which (user, queue, date) triples happen to be in the test DB.

The route layer being exercised:
  - ``resource_details_user_subtree`` at
    ``webapp/dashboards/user/blueprint.py``
  - ``resource_details_day_subtree`` at the same module.

Auth is via the standard ``auth_client`` fixture (logged in as
``benkirk``, which has ``VIEW_PROJECTS`` via the user-permission
override). Required-param failures should 400; missing project 404;
the decorator (``require_project_access``) supplies 403 for
non-member non-admin users, exercised separately.
"""
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def _mock_user_breakdown(monkeypatch):
    """Patch get_user_queue_breakdown_for_project to return predictable rows."""
    captured = {'kwargs': None}

    def _fake(session, projcode, resource, start, end, username=None):
        captured['kwargs'] = {
            'projcode': projcode, 'resource': resource,
            'start': start, 'end': end, 'username': username,
        }
        # Mirror the shape get_user_queue_breakdown_for_project returns
        # filtered to one user: a single dict with nested queues->dates.
        if username:
            # Multi-queue user — exercises the template's branching path
            # that emits queue / date sub-rows. Single_triple users
            # never reach this route (the main page renders them inline).
            return [{
                'username': username,
                'jobs': 20, 'core_hours': 150.0, 'charges': 80.0,
                'queues': [
                    {
                        'queue': 'main',
                        'jobs': 10, 'core_hours': 100.0, 'charges': 50.0,
                        'dates': [{'date': '2026-04-01',
                                   'jobs': 10, 'core_hours': 100.0, 'charges': 50.0}],
                    },
                    {
                        'queue': 'gpu',
                        'jobs': 10, 'core_hours': 50.0, 'charges': 30.0,
                        'dates': [{'date': '2026-04-02',
                                   'jobs': 10, 'core_hours': 50.0, 'charges': 30.0}],
                    },
                ],
            }]
        return []

    monkeypatch.setattr(
        'webapp.dashboards.user.blueprint.get_user_queue_breakdown_for_project',
        _fake,
    )
    return captured


@pytest.fixture
def _mock_daily_breakdown(monkeypatch):
    """Patch get_daily_breakdown_for_project to return predictable rows."""
    captured = {'kwargs': None}

    def _fake(session, projcode, resource, start, end):
        captured['kwargs'] = {
            'projcode': projcode, 'resource': resource,
            'start': start, 'end': end,
        }
        return [{
            'date': start.strftime('%Y-%m-%d'),
            'month': start.strftime('%Y-%m'),
            'jobs': 7, 'core_hours': 50.0, 'charges': 25.0,
            'user_count': 2,
            'rows': [
                {'username': 'alice', 'queue': 'main',
                 'total_jobs': 4, 'total_core_hours': 30.0, 'total_charges': 15.0},
                {'username': 'bob',   'queue': 'gpu',
                 'total_jobs': 3, 'total_core_hours': 20.0, 'total_charges': 10.0},
            ],
        }]

    monkeypatch.setattr(
        'webapp.dashboards.user.blueprint.get_daily_breakdown_for_project',
        _fake,
    )
    return captured


# ---------------------------------------------------------------------------
# /user/resource-details/user-subtree/<projcode>
# ---------------------------------------------------------------------------

class TestUserSubtreeRoute:

    def test_renders_fragment_with_breakdown_rows(
        self, auth_client, active_project, _mock_user_breakdown,
    ):
        resp = auth_client.get(
            f'/user/resource-details/user-subtree/{active_project.projcode}'
            '?resource=Derecho&username=alice'
            '&start_date=2026-04-01&end_date=2026-04-30'
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The fragment renders queue names + date rows for the user.
        assert 'main' in body
        assert 'gpu' in body
        assert '2026-04-01' in body
        assert '2026-04-02' in body

        # Query function received the username scope.
        assert _mock_user_breakdown['kwargs']['username'] == 'alice'

    def test_missing_username_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/user-subtree/{active_project.projcode}'
            '?resource=Derecho'
        )
        assert resp.status_code == 400

    def test_missing_resource_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/user-subtree/{active_project.projcode}'
            '?username=alice'
        )
        assert resp.status_code == 400

    def test_invalid_date_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/user-subtree/{active_project.projcode}'
            '?resource=Derecho&username=alice&start_date=not-a-date'
        )
        assert resp.status_code == 400

    def test_unknown_user_renders_empty_state(
        self, auth_client, active_project, monkeypatch,
    ):
        monkeypatch.setattr(
            'webapp.dashboards.user.blueprint.get_user_queue_breakdown_for_project',
            lambda *a, **kw: [],
        )
        resp = auth_client.get(
            f'/user/resource-details/user-subtree/{active_project.projcode}'
            '?resource=Derecho&username=ghost'
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'No activity for this user' in body


# ---------------------------------------------------------------------------
# /user/resource-details/day-subtree/<projcode>
# ---------------------------------------------------------------------------

class TestDaySubtreeRoute:

    def test_renders_fragment_with_user_queue_rows(
        self, auth_client, active_project, _mock_daily_breakdown,
    ):
        resp = auth_client.get(
            f'/user/resource-details/day-subtree/{active_project.projcode}'
            '?resource=Derecho&date=2026-04-15'
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Both user/queue sub-rows render.
        assert 'alice' in body
        assert 'bob' in body
        assert 'main' in body
        assert 'gpu' in body

        # Query received exactly start=end=2026-04-15.
        kw = _mock_daily_breakdown['kwargs']
        assert kw['start'].strftime('%Y-%m-%d') == '2026-04-15'
        assert kw['end'].strftime('%Y-%m-%d')   == '2026-04-15'

    def test_missing_date_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/day-subtree/{active_project.projcode}'
            '?resource=Derecho'
        )
        assert resp.status_code == 400

    def test_invalid_date_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/day-subtree/{active_project.projcode}'
            '?resource=Derecho&date=not-a-date'
        )
        assert resp.status_code == 400

    def test_empty_day_renders_empty_state(
        self, auth_client, active_project, monkeypatch,
    ):
        monkeypatch.setattr(
            'webapp.dashboards.user.blueprint.get_daily_breakdown_for_project',
            lambda *a, **kw: [],
        )
        resp = auth_client.get(
            f'/user/resource-details/day-subtree/{active_project.projcode}'
            '?resource=Derecho&date=2026-04-15'
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'No activity for this day' in body


# ---------------------------------------------------------------------------
# /user/resource-details/user-pie/<projcode>  (By User pane chart)
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_user_summary(monkeypatch):
    """Patch get_user_summary_for_project with a multi-user rollup."""
    captured = {'kwargs': None}

    def _fake(session, projcode, resource, start, end):
        captured['kwargs'] = {
            'projcode': projcode, 'resource': resource,
            'start': start, 'end': end,
        }
        return [
            {'username': 'alice', 'jobs': 20, 'core_hours': 150.0, 'charges': 80.0,
             'queue_count': 2, 'date_count': 3, 'any_queue': 'main',
             'any_date': '2026-04-01'},
            {'username': 'bob', 'jobs': 10, 'core_hours': 50.0, 'charges': 30.0,
             'queue_count': 1, 'date_count': 1, 'any_queue': 'gpu',
             'any_date': '2026-04-02'},
        ]

    monkeypatch.setattr(
        'webapp.dashboards.user.blueprint.get_user_summary_for_project', _fake,
    )
    return captured


class TestUserPieRoute:
    """The By User pane's chart fragment — sibling of the Usage Trend one.

    Its wedges must emit ``#sam/user/<username>`` sentinels, because
    svg-chart-links.js routes that prefix (and only that prefix) to the
    Usage-by-User table row. A renamed sentinel breaks the drill-down
    silently, so it is asserted rather than assumed.
    """

    def test_renders_pie_with_user_sentinels(
        self, auth_client, active_project, _mock_user_summary,
    ):
        resp = auth_client.get(
            f'/user/resource-details/user-pie/{active_project.projcode}'
            '?resource=Derecho&start_date=2026-04-01&end_date=2026-04-30'
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '#sam/user/alice' in body
        assert '#sam/user/bob' in body
        assert _mock_user_summary['kwargs']['resource'] == 'Derecho'

    def test_missing_resource_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/user-pie/{active_project.projcode}'
        )
        assert resp.status_code == 400

    def test_invalid_date_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/user-pie/{active_project.projcode}'
            '?resource=Derecho&start_date=not-a-date'
        )
        assert resp.status_code == 400

    def test_no_activity_renders_empty_state(
        self, auth_client, active_project, monkeypatch,
    ):
        monkeypatch.setattr(
            'webapp.dashboards.user.blueprint.get_user_summary_for_project',
            lambda *a, **kw: [],
        )
        resp = auth_client.get(
            f'/user/resource-details/user-pie/{active_project.projcode}'
            '?resource=Derecho'
        )
        assert resp.status_code == 200
        assert 'No user activity recorded' in resp.get_data(as_text=True)

    def test_unsupported_metric_clamps_to_charges(
        self, auth_client, active_project, _mock_user_summary,
    ):
        """A persisted `metric` a resource can't serve must not 400.

        The metric lives in an app-wide localStorage family shared with other
        surfaces, so a stale value arrives routinely; the pane clamps.
        """
        resp = auth_client.get(
            f'/user/resource-details/user-pie/{active_project.projcode}'
            '?resource=Derecho&metric=bogus'
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ?usage_tab= — which pane the usage card opens on
# ---------------------------------------------------------------------------

class TestUsageTabParsing:
    """`usage_tab` is server-side because only the open pane's chart carries
    ``hx-trigger="load"`` — an unvalidated value would render a tab strip with
    nothing active and fetch no chart at all.
    """

    @pytest.mark.parametrize('raw,expected', [
        ('history', 'history'),
        ('byuser', 'byuser'),
        ('', 'history'),
        ('nonsense', 'history'),
        (None, 'history'),
    ])
    def test_valid_and_unknown_values(self, app, raw, expected):
        from webapp.dashboards.user.blueprint import _parse_usage_tab

        qs = '' if raw is None else f'?usage_tab={raw}'
        with app.test_request_context(f'/user/resource-details/X{qs}'):
            assert _parse_usage_tab(show_by_user=True) == expected

    def test_byuser_clamps_when_pane_is_hidden(self, app):
        """A single-user project renders no By User button; a stale link
        selecting it must fall back rather than activate a missing tab."""
        from webapp.dashboards.user.blueprint import _parse_usage_tab

        with app.test_request_context(
            '/user/resource-details/X?usage_tab=byuser'
        ):
            assert _parse_usage_tab(show_by_user=False) == 'history'


# ---------------------------------------------------------------------------
# /user/resource-details/<projcode>  (main page — access control)
# ---------------------------------------------------------------------------

class TestResourceDetailsAccessControl:
    """The main page must enforce ``@require_project_access(include_ancestors=True)``
    like its sibling partials. Regression guard for the pre-hardening
    defect where the page was ``@login_required`` only and any
    authenticated user could read any project's usage / per-user charge
    breakdown / allocation history by changing the query string.
    """

    def test_non_member_is_forbidden(self, non_admin_client, session):
        from sam import User, Project

        # Mirror the non_admin_client fixture's user selection so we can
        # find a project this no-permission user is definitely not on.
        user = (
            session.query(User)
            .filter(User.active == True, User.username != "benkirk")
            .order_by(User.user_id)
            .first()
        )
        member_projcodes = {p.projcode for p in user.all_projects} or {"__none__"}
        target = (
            session.query(Project)
            .filter(Project.is_active, Project.projcode.notin_(member_projcodes))
            .order_by(Project.projcode)
            .first()
        )
        assert target is not None, (
            "snapshot needs an active project the non-admin user isn't on"
        )

        resp = non_admin_client.get(
            f'/user/resource-details/{target.projcode}?resource=Derecho'
        )
        assert resp.status_code == 403

    def test_authorized_user_is_not_forbidden(self, auth_client, active_project):
        # benkirk holds VIEW_PROJECTS system-wide -> the decorator admits.
        # 200 when the resource resolves, else a friendly redirect for a
        # missing/mismatched resource — but never 401/403.
        resp = auth_client.get(
            f'/user/resource-details/{active_project.projcode}?resource=Derecho'
        )
        assert resp.status_code not in (401, 403)
