"""Read side of the LDAP sync API (``sam.queries.ldapsync``): the rules each collection carries."""

from datetime import datetime, timedelta

import os

import pytest

from factories import (
    make_account,
    make_adhoc_group,
    make_adhoc_system_account_entry,
    make_allocation,
    make_allocation_type,
    make_institution,
    make_project,
    make_resource,
    make_user,
    make_user_institution,
    make_user_organization,
    next_int,
)
from sam.core.users import LoginType
from sam.queries import ldapsync as q
from sam.security.access import AccessBranch, AccessBranchResource
from sam.schemas.ldapsync import UserSyncSchema


def _login_type_id(session, name):
    return session.query(LoginType).filter_by(type=name).one().login_type_id


_WORKER = os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw')


def _short_name():
    """A unique username that fits adhoc_system_account_entry.username (12 chars)."""
    return f'ls{_WORKER}x{next_int("ldapsync_user"):06d}'


def _one_user(session, user):
    return q.user_by_unix_uid(session, user.unix_uid)


class TestUser:

    def test_stamped_for_deactivation_still_reads_active(self, session):
        """Legacy: ``active = users.active OR deactivate IS NOT NULL`` (pending, not finished)."""
        pending = make_user(session, active=False, deactivate=datetime.now())
        gone = make_user(session, active=False)
        assert _one_user(session, pending)['active'] is True
        assert _one_user(session, gone)['active'] is False

    def test_affiliations_carry_history_but_ids_only_open_rows(self, session):
        user = make_user(session, upid=True)
        open_row = make_user_institution(session, user=user)
        make_user_institution(session, user=user,
                              end_date=datetime.now() - timedelta(days=30))
        row = _one_user(session, user)
        assert len(row['collaborations']) == 2
        assert {c['upid'] for c in row['collaborations']} == {user.upid}
        assert row['institution_ids'] == [open_row.institution_id]

    def test_preferred_name_is_nickname_else_first_name(self, session):
        nick = make_user(session, first_name='Jane', nickname='JJ')
        blank = make_user(session, first_name='Jane', nickname='  ')
        assert _one_user(session, nick)['preferred_name'] == 'JJ'
        assert _one_user(session, blank)['preferred_name'] == 'Jane'

    def test_position_end_reads_as_end_of_day(self, session):
        """Legacy's ``EndDateTimeUserType`` serves a stored mid-day position end at 23:59:59."""
        user = make_user(session, upid=True)
        make_user_organization(session, user=user, end_date=datetime(2024, 8, 2, 11, 6, 10))
        make_user_organization(session, user=user)
        ends = {p['end_date'] for p in _one_user(session, user)['positions']}
        assert ends == {datetime(2024, 8, 2, 23, 59, 59), None}

    def test_dates_serialize_as_epoch_millis(self, session):
        user = make_user(session, upid=True)
        start = datetime(2025, 3, 9, 12, 0, 0)       # noon MDT, the day US DST began
        make_user_institution(session, user=user, start_date=start)
        dumped = UserSyncSchema().dump(_one_user(session, user))
        assert dumped['collaborations'][0]['startDate'] == 1741543200000


class TestGroups:

    def test_members_resolve_case_insensitively(self, session):
        """Legacy mapped exact-case and dropped a mismatch (bug B13)."""
        person = make_user(session, username=_short_name(), upid=True)
        role = make_user(session, username=_short_name(), contact_person_upid=person.upid,
                         login_type_id=_login_type_id(session, 'role_login'))
        group = make_adhoc_group(session)
        make_adhoc_system_account_entry(session, group, person.username.upper())
        make_adhoc_system_account_entry(session, group, role.username)
        make_adhoc_system_account_entry(session, group, 'nosuchuser')
        row = next(g for g in q.groups(session) if g['posix_gid'] == group.unix_gid)
        assert row['upids'] == [person.upid]
        assert row['rolenames'] == [role.username]
        assert 'nosuchuser' in row['usernames']
        assert row['tags'] == ['hpc']
        assert row['key'] == row['name'] == group.group_name


@pytest.fixture
def hpc_branch(session):
    return session.query(AccessBranch).filter_by(name='hpc').one()


def _branch_project(session, branch, *, lead_active=True, facility_name=None,
                    end_date=None):
    lead = make_user(session, upid=True, active=lead_active,
                     login_type_id=_login_type_id(session, 'user_login'))
    kwargs = {}
    if facility_name:
        kwargs['allocation_type'] = make_allocation_type(session, facility_name=facility_name)
    project = make_project(session, lead=lead, **kwargs)
    resource = make_resource(session)
    session.add(AccessBranchResource(access_branch_id=branch.access_branch_id,
                                     resource_id=resource.resource_id))
    account = make_account(session, project=project, resource=resource)
    make_allocation(session, account=account,
                    end_date=end_date or datetime.now() + timedelta(days=30))
    return project, lead


def _project_row(session, project):
    return next(p for p in q.project_groups(session) if p['name'] == project.projcode)


class TestProjectGroups:

    def test_member_lead_and_tags(self, session, hpc_branch):
        project, lead = _branch_project(session, hpc_branch)
        row = _project_row(session, project)
        assert row['key'] == project.projcode.lower()
        assert row['upids'] == [lead.upid]
        assert row['tags'][0] == 'hpc'
        assert 'exclude-from-google' in row['tags']

    def test_branch_tag_does_not_need_an_active_member(self, session, hpc_branch):
        """Deviation D14: legacy tagged only projects with an active member (bug B3)."""
        project, lead = _branch_project(session, hpc_branch, lead_active=False)
        row = _project_row(session, project)
        assert 'hpc' in row['tags']
        assert row['upids'] == [lead.upid]       # lead and admin are always members

    def test_expired_past_grace_is_untagged(self, session, hpc_branch):
        project, _ = _branch_project(session, hpc_branch,
                                     end_date=datetime.now() - timedelta(days=200))
        assert _project_row(session, project)['tags'] == ['exclude-from-google']

    def test_auto_renew_tag_follows_facility_code(self, session, hpc_branch):
        project, _ = _branch_project(session, hpc_branch, facility_name='NCAR')
        assert _project_row(session, project)['tags'][-1] == 'auto-renewed-project'

    def test_since_drops_older_projects(self, session, hpc_branch):
        project, _ = _branch_project(session, hpc_branch)
        future = datetime.now() + timedelta(days=1)
        assert all(p['name'] != project.projcode
                   for p in q.project_groups(session, since=future))


class TestReadSince:

    @pytest.mark.parametrize('raw, expected', [
        ('1700000000', datetime(2023, 11, 14, 15, 13, 20)),
        ('1700000000000', datetime(2023, 11, 14, 15, 13, 20)),
        ('junk', None), (None, None),
    ])
    def test_seconds_or_millis(self, raw, expected):
        assert q.read_since(raw) == expected


def test_institutions_carry_state_from_state_prov(session):
    inst = make_institution(session)
    row = next(i for i in q.institutions(session) if i['institution_id'] == inst.institution_id)
    assert row['country'] is None and row['state'] is None


def test_status_names_every_table(session):
    status = q.sync_status(session)
    assert set(status) == {'institution_update_time', 'organization_update_time',
                           'user_update_time', 'group_update_time',
                           'gid_allocation_update_time', 'access_branches'}
    assert 'hpc' in status['access_branches']
