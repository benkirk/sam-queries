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


def test_the_news_notice_waits_for_the_cutover_floor(monkeypatch):
    """SAMuel mails from the cutover on; before it the award is XRAS's to send."""
    from datetime import datetime
    monkeypatch.setenv('XRAS_AUTO_NOTIFY', '1')
    monkeypatch.setenv('SAM_TASKS_XRAS_NOTICES_SINCE', '2033-11-16T08:00')
    assert xras_activation.auto_notify_armed(datetime(2033, 11, 16, 7, 59)) is False
    assert xras_activation.auto_notify_armed(datetime(2033, 11, 16, 8, 0)) is True
    monkeypatch.setenv('SAM_TASKS_XRAS_NOTICES_SINCE', '')
    assert xras_activation.auto_notify_armed(datetime(2000, 1, 1)) is True


class TestTheRosterInvites:
    """2b: the people a New named that XRAS could not hold, invited at activation."""

    def _filed(self, session, project, roster):
        from datetime import datetime
        from sam.integration.xras_submission import XrasSubmission
        from sam.manage.xras_submission import ActionRequest
        draft = ActionRequest(action_type='New', opportunity_id=1, roster=roster)
        XrasSubmission.create(session, action_type='New', created_by='pi1', xa_user='pi1',
                              eula_sha='a' * 40, eula_accepted_at=datetime.now(),
                              payload=draft.to_payload(), project_id=project.project_id)

    def test_each_invitee_is_queued_under_the_lead_once(self, session):
        project = make_project(session, active=True)
        self._filed(session, project, (
            {'first_name': 'Ada', 'last_name': 'Lovelace', 'email': 'zz.ada@example.invalid',
             'username': None},
            {'first_name': 'In', 'last_name': 'Xras', 'email': 'zz.x@example.invalid',
             'username': 'inxras'}))
        (row,) = xras_activation.invite_submission_roster(session, project, by='benkirk')
        assert (row.email, row.purpose, row.sponsor_user_id, row.project_id) == (
            'zz.ada@example.invalid', 'enrollment', project.lead.user_id, project.project_id)
        assert project.projcode in row.comment
        assert xras_activation.invite_submission_roster(session, project, by='benkirk') == []

    def test_a_project_no_submission_named_invites_no_one(self, session):
        project = make_project(session, active=True)
        assert xras_activation.invite_submission_roster(session, project, by='benkirk') == []
