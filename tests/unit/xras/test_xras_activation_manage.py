"""`sam.manage.xras_activation`: the card's Activate and the post's auto-activation."""
from factories import make_project

from sam.integration.xras import XrasActivationEvent
from sam.manage import xras_activation


def _events(session, project):
    return [(e.event_type, e.created_by) for e in session.query(XrasActivationEvent)
            .filter_by(project_id=project.project_id)]


def test_it_activates_and_records_who(session):
    project = make_project(session, active=False)
    event = xras_activation.activate_project(session, project, by='xras:xras-api')
    assert project.is_active and project.inactivate_time is None
    assert event is not None
    assert _events(session, project) == [('activated', 'xras:xras-api')]


def test_an_active_project_writes_nothing(session):
    project = make_project(session, active=True)
    assert xras_activation.activate_project(session, project, by='benkirk') is None
    assert _events(session, project) == []


def test_the_posted_path_reports_each_outcome(session):
    project = make_project(session, active=False)
    activate = xras_activation.activate_posted_project
    assert activate(session, project.projcode, by='xras:api') == 'activated'
    assert activate(session, project.projcode, by='xras:api') == 'already active'
    assert activate(session, 'ZZZZ9999', by='xras:api') == 'not found'


def test_the_lever_is_off_under_the_suite(app):
    with app.app_context():
        assert xras_activation.auto_activate_armed() is False
