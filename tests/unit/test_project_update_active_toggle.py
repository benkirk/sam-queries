"""The admin project-edit Active / Charging-Exempt checkboxes must persist.

Drives `_ProjectUpdateHandler.perform()` directly on the isolated session
(a route POST would commit through Flask-SQLAlchemy's `db.session`). The FK
check and governance gate are patched out — neither is what this exercises.
The bug: the handler loads `partial=True`, which skips `load_default`, so an
unchecked box was dropped and the flag never changed.
"""

from datetime import datetime

import pytest

from webapp.dashboards.admin import projects_routes as pr
from factories import make_project

pytestmark = pytest.mark.unit


def _perform(app, project, form):
    handler = pr._ProjectUpdateHandler(project=project)
    with app.test_request_context('/', method='POST', data=form):
        handler.perform(handler.clean(handler.load(handler.form_input())))


def _base_form(project, **extra):
    form = {
        'title': project.title,
        'area_of_interest_id': str(project.area_of_interest_id),
        'project_lead_user_id': str(project.project_lead_user_id),
    }
    form.update(extra)
    return form


@pytest.fixture
def as_governance(monkeypatch):
    monkeypatch.setattr(pr, 'can_edit_project_governance', lambda u, p: True)
    monkeypatch.setattr(pr, 'validate_fk_existence', lambda *a, **k: None)


def test_unchecked_active_deactivates_and_stamps(session, app, as_governance):
    project = make_project(session, active=True)
    assert project.inactivate_time is None

    _perform(app, project, _base_form(project))  # no 'active' key == unchecked

    assert project.active is False
    assert project.inactivate_time is not None


def test_checked_active_reactivates_and_clears_stamp(session, app, as_governance):
    project = make_project(session, active=True)
    project.deactivate(when=datetime(2025, 1, 2, 3, 4, 5))
    assert project.active is False and project.inactivate_time is not None

    _perform(app, project, _base_form(project, active='1'))

    assert project.active is True
    assert project.inactivate_time is None


def test_no_active_change_preserves_stamp(session, app, as_governance):
    project = make_project(session, active=True)
    stamp = datetime(2025, 6, 7, 8, 9, 10)
    project.deactivate(when=stamp)

    # Box left unchecked (matches the inactive state) while editing the title.
    _perform(app, project, _base_form(project, title='Renamed while inactive'))

    assert project.active is False
    assert project.inactivate_time == stamp
    assert project.title == 'Renamed while inactive'


def test_unchecked_charging_exempt_clears_flag(session, app, as_governance):
    project = make_project(session, active=True)
    project.charging_exempt = True
    session.flush()

    _perform(app, project, _base_form(project))  # no 'charging_exempt' key

    assert project.charging_exempt is False


def test_non_governance_cannot_flip_active(session, app, monkeypatch):
    monkeypatch.setattr(pr, 'can_edit_project_governance', lambda u, p: False)
    monkeypatch.setattr(pr, 'validate_fk_existence', lambda *a, **k: None)
    project = make_project(session, active=True)

    # A crafted submit carrying no 'active' key must not deactivate the project.
    _perform(app, project, _base_form(project))

    assert project.active is True
    assert project.inactivate_time is None
