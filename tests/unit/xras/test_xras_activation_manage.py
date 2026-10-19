"""`sam.manage.xras_activation`: the card's Activate and the post's auto-activation."""
import pytest
from factories import make_email_address, make_project

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


class _Notifier:
    """Captures the messages; answers each with *status*."""

    def __init__(self, status='sent'):
        self.status, self.sent = status, []

    def send_many(self, messages):
        from types import SimpleNamespace
        self.sent.extend(messages)
        return [SimpleNamespace(status=self.status, message=m) for m in messages]


class TestTheNoticeAfterActivation:
    """Phase C: the New's award mail right after the post's activation."""

    @pytest.fixture(autouse=True)
    def _no_reports_feed(self, monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.comments.approver_comment_for_action',
                            lambda action, **_: 'Approved as recommended.')

    def _project(self, session):
        project = make_project(session, active=True)
        make_email_address(session, project.lead, email='pi@example.edu')
        session.expire(project.lead, ['email_addresses'])
        return project

    def test_one_letter_is_sent_and_recorded(self, session):
        project, notifier = self._project(session), _Notifier()
        outcome = xras_activation.notify_activated_project(
            session, project.projcode, notifier, by='xras:XRAS', xras_action_log_id=None)
        assert outcome == 'sent'
        (message,) = notifier.sent
        assert (message.kind, message.recipient.address) == ('xras_activation', 'pi@example.edu')
        assert message.context['approver_comment'] == 'Approved as recommended.'
        assert _events(session, project) == [('notified', 'xras:XRAS')]

    def test_nothing_delivered_records_nothing(self, session):
        project = self._project(session)
        outcome = xras_activation.notify_activated_project(
            session, project.projcode, _Notifier('suppressed'), by='xras:XRAS',
            xras_action_log_id=None)
        assert outcome == 'nothing sent' and _events(session, project) == []

    def test_an_unknown_projcode_is_not_found(self, session):
        assert xras_activation.notify_activated_project(
            session, 'ZZZZ9999', _Notifier(), by='xras:XRAS',
            xras_action_log_id=None) == 'not found'


def test_the_notify_lever_is_off_under_the_suite(app):
    with app.app_context():
        assert xras_activation.auto_notify_armed() is False
