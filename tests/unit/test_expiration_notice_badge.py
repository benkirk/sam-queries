"""The last-notified badge on the admin Project Expirations cards.

`render_project_card` is **shared** with the user dashboard, so the assertion
that carries the most weight here is the negative one: a PI must not be shown
our mail log. The macro gates on the presence of `project_data.notification`,
and only `_build_expiration_project_data` ever sets it — these tests render
the macro both ways to prove the gate holds.

The Playwright pass covers the same states in a real browser plus computed
contrast in both themes; this tier is what fails in CI if someone widens the
gate.
"""

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.unit

MACRO = 'dashboards/user/partials/project_card.html'


class _Project:
    """The handful of attributes the badge region of the macro touches."""

    def __init__(self, projcode='SCSG0001'):
        self.projcode = projcode
        self.title = 'A Test Project'
        self.project_id = 1
        self.lead = None
        self.admin = None
        self.active = True
        self.is_active = True


class _User:
    user_id = 999
    username = 'someone'


def _render(app, notification=...):
    """Render the card macro, with or without the `notification` key.

    `notification=...` (the default) means "do not set the key at all" — the
    user-dashboard shape. `None` cannot express that, because a key present
    and null is a different thing from a key never set, and the whole gate
    turns on the difference.
    """
    # No resources: the badge does not depend on them, and a half-populated
    # resource dict would have this module failing on the card's allocation
    # table — which has its own coverage — rather than on anything it is for.
    project_data = {'project': _Project(), 'resources': [], 'users': []}
    if notification is not ...:
        project_data['notification'] = notification

    with app.test_request_context('/'):
        from flask import render_template_string

        from webapp.utils.rbac import Permission
        return render_template_string(
            "{%% from '%s' import render_project_card with context %%}"
            "{{ render_project_card(pd, 0, user, 80, 95) }}" % MACRO,
            pd=project_data, user=_User(),
            # Normally supplied by the app's context processors. Stubbed to
            # False so the card's action buttons stay out of the way — this
            # module is about the badge, and every other branch of the macro
            # has its own coverage.
            Permission=Permission,
            has_permission=lambda *_a, **_k: False,
            can_act_on_project=lambda *_a, **_k: False)


def _notice(**over):
    base = {'notified': True,
            'notified_time': datetime.now() - timedelta(days=3),
            'notified_age': timedelta(days=3),
            'delivered_count': 4,
            'failed_count': 0}
    base.update(over)
    return base


class TestTheBadgeIsAdminOnly:

    def test_it_is_absent_when_the_key_was_never_set(self, app):
        """WARNING: THE assertion. `render_project_card` is shared, and the user
        dashboard never sets `notification` — a PI has no business seeing
        which of their colleagues we emailed, or when."""
        html = _render(app)
        assert 'Notified' not in html
        assert 'Not notified' not in html

    def test_it_appears_when_the_admin_path_sets_it(self, app):
        assert 'Notified' in _render(app, _notice())


class TestTheThreeStates:

    def test_notified_renders_the_age_and_the_recipient_count(self, app):
        html = _render(app, _notice())
        assert 'Notified 3 days ago' in html
        assert 'delivered to 4 recipient(s)' in html

    def test_never_notified_is_an_explicit_state(self, app):
        """Not a blank, and not an em dash from `fmt_ago`'s null path — a
        blank reads as "the badge is broken"."""
        html = _render(app, _notice(notified=False, notified_time=None,
                                    notified_age=None, delivered_count=0))
        assert 'Not notified' in html
        assert 'Notified 3 days ago' not in html

    def test_failures_add_their_own_badge(self, app):
        html = _render(app, _notice(failed_count=2))
        assert '2 failed' in html
        assert 'Notified 3 days ago' in html, 'both states can be true at once'

    def test_a_project_with_only_failures_shows_both(self, app):
        """Nothing reached anybody, and something went wrong doing it."""
        html = _render(app, _notice(notified=False, notified_time=None,
                                    notified_age=None, delivered_count=0,
                                    failed_count=3))
        assert 'Not notified' in html
        assert '3 failed' in html

    def test_no_failures_renders_no_failure_badge(self, app):
        assert 'failed' not in _render(app, _notice())


class TestTheTooltipCarriesTheAbsoluteDate:

    def test_a_stale_notice_is_self_evident(self, app):
        """Accepted imprecision: the badge shows the newest notice for the
        project whatever expiration it referred to, so a project notified
        about a prior year reads "Notified 400 days ago". The absolute date
        in the tooltip is what makes that legible rather than misleading."""
        old = datetime(2024, 9, 30, 9, 0)
        html = _render(app, _notice(notified_time=old,
                                    notified_age=datetime.now() - old))
        assert '2024-09-30' in html


class TestTheRouteSetsItForEveryProject:

    def test_build_expiration_project_data_sets_the_key(self, app, session,
                                                        monkeypatch):
        """Including never-notified projects — the macro needs all three
        states, and a missing key would collapse two of them."""
        from webapp.dashboards.admin import blueprint

        project = _Project('QQQQ0001')
        monkeypatch.setattr(
            blueprint, 'get_projects_dashboard_data',
            lambda _s, projects: [{'project': p, 'resources': []} for p in projects])

        with app.test_request_context('/'):
            built = blueprint._build_expiration_project_data(
                [(project, None, 'Derecho', 20)])

        assert built[0]['notification']['notified'] is False

    def test_it_is_one_bulk_query_not_one_per_project(self, app, session,
                                                      monkeypatch):
        """The builder does one bulk `get_expiration_notice_status` for the
        whole page — not a per-project round trip (batched via
        `get_projects_dashboard_data`)."""
        from webapp.dashboards.admin import blueprint

        calls = []
        real = blueprint.get_expiration_notice_status
        monkeypatch.setattr(blueprint, 'get_expiration_notice_status',
                            lambda s, codes: calls.append(list(codes)) or real(s, codes))
        monkeypatch.setattr(
            blueprint, 'get_projects_dashboard_data',
            lambda _s, projects: [{'project': p, 'resources': []} for p in projects])

        rows = [(_Project(f'BULK{i:04d}'), None, 'Derecho', 20)
                for i in range(5)]
        with app.test_request_context('/'):
            blueprint._build_expiration_project_data(rows)

        assert len(calls) == 1
        assert len(calls[0]) == 5
