"""`sam.queries.notification_previews` — real messages for one project."""

import json
from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from factories.xras import make_xras_action, make_xras_key_mapping

from sam.core.users import EmailAddress
from sam.queries.notification_previews import (
    expiring_rows_for_project, is_project_kind, messages_for_project,
)


def _with_email(session, user, address):
    session.add(EmailAddress(user_id=user.user_id, email_address=address,
                             is_primary=True, active=True))
    session.flush()
    session.refresh(user)
    return user


@pytest.fixture
def project(session):
    lead = _with_email(session, make_user(session), 'lead@example.edu')
    proj = make_project(session, title='A Test Project', lead=lead)
    session.expire(proj)
    return proj


def _dated_allocation(session, project, *, days, **kwargs):
    account = make_account(session, project=project, resource=make_resource(session))
    return account, make_allocation(
        session, account=account, amount=1_000.0,
        start_date=datetime.now() - timedelta(days=300),
        end_date=datetime.now() + timedelta(days=days), **kwargs)


class TestIsProjectKind:

    @pytest.mark.parametrize('kind,expected', [
        ('expiration', True), ('xras_activation', True),
        ('xras_supplement', True), ('task_summary', False),
    ])
    def test_families(self, kind, expected):
        assert is_project_kind(kind) is expected


class TestExpiringRows:

    def test_one_row_per_dated_account(self, session, project):
        account, allocation = _dated_allocation(session, project, days=12)
        session.expire(project)
        rows = expiring_rows_for_project(project)
        assert len(rows) == 1
        proj, alloc, resource_name, days = rows[0]
        assert proj is project and alloc is allocation
        assert resource_name == account.resource.resource_name
        assert days in (11, 12)

    def test_open_ended_and_deleted_allocations_are_skipped(self, session, project):
        account = make_account(session, project=project,
                               resource=make_resource(session))
        open_ended = make_allocation(session, account=account, amount=1.0,
                                     start_date=datetime.now() - timedelta(days=10))
        open_ended.end_date = None
        _account, gone = _dated_allocation(session, project, days=5)
        gone.deleted = True
        session.flush()
        session.expire(project)
        assert expiring_rows_for_project(project) == []

    def test_an_expired_project_still_yields_rows_with_negative_days(
            self, session, project):
        _dated_allocation(session, project, days=-30)
        session.expire(project)
        (_p, _a, _r, days), = expiring_rows_for_project(project)
        assert days <= -29


class TestMessagesForProject:

    def test_a_task_kind_is_refused(self, session, project):
        with pytest.raises(ValueError):
            messages_for_project(session, 'task_summary', project, requested_by='t')

    def test_expiration_builds_the_leads_copy(self, session, project):
        _dated_allocation(session, project, days=12)
        session.expire(project)
        messages = messages_for_project(session, 'expiration', project,
                                        requested_by='pytest')
        (message,) = [m for m in messages if m.recipient.role == 'lead']
        assert message.kind == 'expiration'
        assert message.recipient.address == 'lead@example.edu'
        assert message.context['project_code'] == project.projcode
        assert message.context['resources'][0]['days_remaining'] in (11, 12)

    def test_expiration_without_dated_allocations_is_empty(self, session, project):
        assert messages_for_project(session, 'expiration', project,
                                    requested_by='pytest') == []

    def test_xras_forces_the_templates_kind_and_only_matching_increments(
            self, session, project):
        key = make_xras_key_mapping(session).xras_key
        payload = json.dumps({'resources': [
            {'resourceRepositoryKey': key, 'awardedAmount': '50000'}]})
        make_xras_action(session, status='processed', action_type='Adjustment',
                         service='adjust', request_number=project.projcode,
                         projcode_result=project.projcode, payload=payload)
        supplement = messages_for_project(session, 'xras_supplement', project,
                                          requested_by='pytest')
        assert supplement and supplement[0].kind == 'xras_supplement'
        assert supplement[0].context['added'] == []
        adjustment = messages_for_project(session, 'xras_adjustment', project,
                                          requested_by='pytest')
        assert adjustment[0].context['changes'][0]['amount'].startswith('+')

    def test_xras_without_an_addressable_lead_is_empty(self, session):
        proj = make_project(session, lead=make_user(session))
        assert messages_for_project(session, 'xras_activation', proj,
                                    requested_by='pytest') == []

    def test_xras_without_any_action_still_previews(self, session, project):
        messages = messages_for_project(session, 'xras_update', project,
                                        requested_by='pytest')
        assert messages[0].kind == 'xras_update'
        assert messages[0].context['action_type'] is None
