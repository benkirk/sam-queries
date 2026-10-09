"""Collaborator keep-alive (``sam.queries.user_lifecycle``): who is emitted, and with what expiry."""

from datetime import datetime, timedelta

from factories import (
    make_account,
    make_allocation,
    make_organization,
    make_project,
    make_resource,
    make_user,
    make_user_institution,
    make_user_organization,
)
from sam.accounting.accounts import AccountUser
from sam.core.users import LoginType
from sam.queries.user_lifecycle import collab_expiry_updates
from sam.schemas.ldapsync import ActiveUserStatusSchema

NOW = datetime.now().replace(microsecond=0)
ALLOC_END = (NOW + timedelta(days=400)).replace(hour=23, minute=59, second=59)


def _collaborator(session, *, collab_end=None):
    login = session.query(LoginType).filter_by(type='user_login').one()
    user = make_user(session, upid=True, login_type_id=login.login_type_id)
    make_user_institution(session, user=user, end_date=collab_end)
    return user


def _project_with_allocation(session):
    project = make_project(session)
    account = make_account(session, project=project, resource=make_resource(session))
    make_allocation(session, account=account, end_date=ALLOC_END)
    return project, account


def _status(session, user):
    return next((s for s in collab_expiry_updates(session, now=NOW)
                 if s['user_id'] == user.user_id), None)


def test_member_takes_the_allocation_end(session):
    user = _collaborator(session)
    _, account = _project_with_allocation(session)
    session.add(AccountUser(account_id=account.account_id, user_id=user.user_id,
                            start_date=NOW - timedelta(days=10)))
    session.flush()
    status = _status(session, user)
    assert status['type'] == 'Collaborator' and status['active_collaborator'] is True
    assert status['nominal_expiry'] == ALLOC_END.strftime('%Y-%m-%d')
    assert status['dated_associations'][0]['type'] == 'Allocation'


def test_project_admin_counts(session):
    """Legacy indexed the lead twice and never the admin."""
    user = _collaborator(session)
    project, _ = _project_with_allocation(session)
    project.project_admin_user_id = user.user_id
    session.flush()
    types = {a['type'] for a in _status(session, user)['dated_associations']}
    assert 'Project Admin Project Allocation' in types


def test_staff_are_not_collaborators(session):
    user = _collaborator(session)
    make_user_organization(session, user=user, organization=make_organization(session))
    assert _status(session, user) is None


def test_later_directory_end_needs_no_update(session):
    user = _collaborator(session, collab_end=NOW + timedelta(days=2000))
    _, account = _project_with_allocation(session)
    session.add(AccountUser(account_id=account.account_id, user_id=user.user_id,
                            start_date=NOW - timedelta(days=10)))
    session.flush()
    assert _status(session, user) is None


def test_no_association_gets_the_grace_period(session):
    user = _collaborator(session)
    status = _status(session, user)
    assert status['dated_associations'][0]['type'] == '(None)'
    assert status['nominal_expiry'] == (NOW + timedelta(days=90)).strftime('%Y-%m-%d')


def test_two_open_collaborations_one_without_end(session):
    """Legacy threw on the null end date and the whole endpoint answered 500."""
    user = _collaborator(session)
    make_user_institution(session, user=user, end_date=NOW + timedelta(days=30))
    assert _status(session, user)['current_collaboration_end_date'] is None


def test_wire_keys_are_legacy_order(session):
    user = _collaborator(session)
    dumped = ActiveUserStatusSchema().dump(_status(session, user))
    assert list(dumped) == ['userId', 'upid', 'unixUid', 'username',
                            'currentCollaborationEndDate', 'currentPositionEndDate', 'type',
                            'datedAssociations', 'nominalExpiry', 'activeCollaborator',
                            'activeStaff']
    assert list(dumped['datedAssociations'][0]) == ['type', 'description', 'endDate']
