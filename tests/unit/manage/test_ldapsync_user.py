"""User and group upserts from ``sam-ldap-syncd`` (``sam.manage.ldapsync.sync_user`` / ``sync_group``)."""

import os
from datetime import datetime, timedelta

import pytest
from marshmallow import ValidationError
from sqlalchemy import event

from factories import (
    make_adhoc_group,
    make_adhoc_system_account_entry,
    make_email_address,
    make_institution,
    make_organization,
    make_phone,
    make_project,
    make_user,
    make_user_institution,
    make_user_organization,
    next_int,
)
from sam.core.groups import AdhocGroup
from sam.core.users import LoginType, User
from sam.manage.ldapsync import SyncValidationError, sync_group, sync_user
from sam.schemas.forms.ldapsync import GroupSyncInput, UserSyncInput

_WORKER = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or '0')
NOW = datetime(2026, 10, 9, 12, 0, 0)


def _ms(dt):
    from sam.dates import to_epoch_millis
    return to_epoch_millis(dt)


def _new_name():
    return f'ls{_WORKER}u{next_int("ldapsync_user_name"):06d}'


def _payload(**overrides):
    body = {'userName': _new_name(), 'unixUid': 70_000_000 + next_int('ldapsync_uid'),
            'upid': 70_000_000 + _WORKER * 100_000 + next_int('ldapsync_upid'),
            'active': True, 'locked': False, 'chargingExempt': False,
            'firstname': 'Jane', 'lastname': 'Doe', 'typeOfLogin': 'user_login',
            'emails': [], 'phones': [], 'collaborations': [], 'positions': []}
    body.update(overrides)
    return body


def _sync(session, body, **kw):
    return sync_user(session, UserSyncInput().load(body), now=NOW, **kw)


def _payload_for(user, **overrides):
    return _payload(**{'userName': user.username, 'unixUid': user.unix_uid,
                       'upid': user.upid, **overrides})


@pytest.fixture
def writes(session):
    """Count INSERT/UPDATE/DELETE statements issued on the test connection."""
    seen = []

    def _listen(conn, cursor, statement, *args):
        if statement.lstrip().split(' ', 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE'):
            seen.append(statement)

    engine = session.get_bind()
    event.listen(engine, 'before_cursor_execute', _listen)
    yield seen
    event.remove(engine, 'before_cursor_execute', _listen)


class TestCreate:

    def test_new_user_with_everything(self, session):
        inst, org = make_institution(session), make_organization(session)
        body = _payload(
            emails=[{'email': 'Jane@Example.edu', 'primary': True}],
            phones=[{'phoneNumber': '303-555-0100', 'extPhoneType': 'ucar office'}],
            collaborations=[{'institutionId': inst.institution_id, 'startDate': _ms(NOW)}],
            positions=[{'organizationId': org.organization_id, 'startDate': _ms(NOW),
                        'endDate': _ms(datetime(2027, 1, 1)), 'idmsUniqueName': 'P-1'}])
        assert _sync(session, body) == body['unixUid']
        user = User.get_by_username(session, body['userName'])
        assert (user.upid, user.unix_uid, user.active) == (body['upid'], body['unixUid'], True)
        assert user.login_type.type == 'user_login'
        assert [e.email_address for e in user.email_addresses] == ['Jane@Example.edu']
        assert user.organizations[0].end_date == datetime(2027, 1, 1, 23, 59, 59)
        assert user.institutions[0].institution_id == inst.institution_id

    def test_upid_held_by_another_username_is_rejected(self, session):
        """Legacy's text, naming the SAM user who holds the upid."""
        holder = make_user(session, upid=True)
        with pytest.raises(SyncValidationError) as exc:
            _sync(session, _payload(upid=holder.upid))
        assert exc.value.messages == [
            f'Upid {holder.upid} matches username {holder.username} '
            '(username change in ID Service?).']

    def test_upid_as_a_numeric_string(self, session):
        body = _payload()
        body['upid'] = str(body['upid'])
        _sync(session, body)
        assert User.get_by_username(session, body['userName']).upid == int(body['upid'])

    @pytest.mark.parametrize('field', ['locked', 'chargingExempt', 'active'])
    def test_null_required_flag_is_a_400(self, field):
        with pytest.raises(ValidationError):
            UserSyncInput().load(_payload(**{field: None}))


class TestUpdate:

    def test_matched_by_username_case_insensitively_identity_untouched(self, session):
        user = make_user(session, upid=True)
        uid, name = user.unix_uid, user.username
        _sync(session, _payload_for(user, userName=name.upper(), unixUid=1, firstname='Renamed'))
        assert (user.first_name, user.unix_uid, user.username) == ('Renamed', uid, name)

    def test_identical_put_writes_nothing(self, session, writes):
        """Legacy restamped modified_time on every affiliation row of every PUT."""
        user = make_user(session, upid=True,
                         login_type_id=session.query(LoginType).filter_by(type='user_login')
                         .one().login_type_id)
        org = make_organization(session)
        row = make_user_organization(session, user=user, organization=org,
                                     end_date=datetime(2027, 1, 1, 23, 59, 59))
        body = _payload_for(user, firstname=user.first_name, lastname=user.last_name,
                            positions=[{'positionId': row.user_organization_id,
                                        'organizationId': org.organization_id,
                                        'startDate': _ms(row.start_date),
                                        'endDate': _ms(row.end_date)}])
        _sync(session, body)
        writes.clear()
        _sync(session, body)
        assert writes == []
        _sync(session, {**body, 'firstname': 'Changed'})     # the listener does see writes
        assert any(w.lstrip().upper().startswith('UPDATE') for w in writes)


class TestActiveTransitions:

    def test_idm_inactive_stamps_and_stays_active(self, session):
        user = make_user(session, upid=True, active=True)
        _sync(session, _payload_for(user, active=False))
        assert (user.active, user.deactivate) == (True, NOW)

    def test_idm_active_clears_a_pending_stamp(self, session):
        user = make_user(session, upid=True, active=True, deactivate=NOW - timedelta(days=1))
        calls = []
        _sync(session, _payload_for(user, active=True), on_reactivate=calls.append)
        assert (user.active, user.deactivate, calls) == (True, None, [])

    def test_idm_active_brings_back_a_finished_user_and_fires_the_hook(self, session):
        user = make_user(session, upid=True, active=False)
        calls = []
        _sync(session, _payload_for(user, active=True), on_reactivate=calls.append)
        assert user.active is True and calls == [user]

    def test_inactive_on_inactive_is_a_noop(self, session):
        user = make_user(session, upid=True, active=False)
        _sync(session, _payload_for(user, active=False))
        assert (user.active, user.deactivate) == (False, None)


class TestChildRows:

    def test_emails_match_case_insensitively_and_orphans_go(self, session):
        user = make_user(session, upid=True)
        keep = make_email_address(session, user, email='jane@example.edu', is_primary=False)
        make_email_address(session, user, email='old@example.edu')
        _sync(session, _payload_for(user, emails=[
            {'email': 'JANE@example.edu', 'primary': True},
            {'email': 'new@example.edu', 'primary': False}]))
        addresses = sorted(e.email_address for e in user.email_addresses)
        assert addresses == ['JANE@example.edu', 'new@example.edu']
        assert keep.is_primary is True

    def test_phones_orphans_go_unknown_type_skipped(self, session):
        user = make_user(session, upid=True)
        make_phone(session, user, number='303-555-0001')
        _sync(session, _payload_for(user, phones=[
            {'phoneNumber': '303-555-0002', 'extPhoneType': 'No Such Type'}]))
        assert user.phones == []

    def test_stale_position_id_updates_the_matching_row(self, session):
        """The 500 legacy answered 73 times since August: an id SAM no longer holds."""
        user = make_user(session, upid=True)
        org = make_organization(session)
        row = make_user_organization(session, user=user, organization=org,
                                     start_date=datetime(2025, 1, 1))
        _sync(session, _payload_for(user, positions=[{
            'positionId': 987654321, 'organizationId': org.organization_id,
            'startDate': _ms(datetime(2025, 1, 1)), 'endDate': _ms(datetime(2026, 6, 30))}]))
        assert len(user.organizations) == 1
        assert row.end_date == datetime(2026, 6, 30, 23, 59, 59)

    def test_rows_missing_from_the_payload_are_kept(self, session):
        user = make_user(session, upid=True)
        make_user_institution(session, user=user)
        _sync(session, _payload_for(user, collaborations=[]))
        assert len(user.institutions) == 1

    def test_unknown_employer_is_skipped_not_fatal(self, session):
        user = make_user(session, upid=True)
        _sync(session, _payload_for(user, collaborations=[
            {'institutionId': 987654321, 'startDate': _ms(NOW)}]))
        assert user.institutions == []


class TestRoleLogin:

    def test_names_come_from_the_contact_person(self, session):
        contact = make_user(session, upid=True, first_name='Pat', last_name='Owner')
        _sync(session, body := _payload(typeOfLogin='role_login', upid=None,
                                        contactPersonUpid=contact.upid, firstname='x'))
        role = User.get_by_username(session, body['userName'])
        assert (role.first_name, role.last_name) == ('Pat', 'Owner')

    def test_unknown_contact_on_a_new_role_login_is_unknown(self, session):
        _sync(session, body := _payload(typeOfLogin='role_login', upid=None,
                                        contactPersonUpid=987654321))
        assert User.get_by_username(session, body['userName']).last_name == 'unknown'


# --- groups ----------------------------------------------------------------

def _group(session, **overrides):
    body = {'key': f'g{_WORKER}x{next_int("ldapsync_group"):06d}',
            'posixGid': 95_000_000 + _WORKER * 100_000 + next_int('ldapsync_gid'),
            'active': True, 'tags': ['hpc', 'glade'], 'usernames': ['alice', 'bob']}
    body.update(overrides)
    data = GroupSyncInput().load(body)
    return sync_group(session, data), body


class TestGroup:

    def test_new_adhoc_group(self, session):
        gid, body = _group(session, key='MixedCase' + str(next_int('ldapsync_group')))
        group = AdhocGroup.get_by_unix_gid(session, gid)
        assert group.group_name == body['key'].lower()
        assert sorted(t.tag for t in group.tags) == ['glade', 'hpc']
        assert sorted((e.access_branch_name, e.username) for e in group.system_accounts) == [
            ('hpc', 'alice'), ('hpc', 'bob')]

    def test_existing_group_renames_and_resyncs_members(self, session):
        group = make_adhoc_group(session)
        make_adhoc_system_account_entry(session, group, 'gone')
        _group(session, key='renamed' + str(next_int('ldapsync_group')),
               posixGid=group.unix_gid, usernames=['carol'])
        assert [e.username for e in group.system_accounts] == ['carol']
        assert group.group_name.startswith('renamed')

    def test_key_held_by_another_gid_is_inconsistent(self, session):
        group = make_adhoc_group(session)
        with pytest.raises(SyncValidationError, match='Inconsistency with group key'):
            _group(session, key=group.group_name)

    def test_no_branch_tag_is_dropped(self, session):
        gid, _ = _group(session, tags=['glade'])
        assert AdhocGroup.get_by_unix_gid(session, gid) is None

    def test_project_without_gid_receives_it(self, session):
        project = make_project(session)
        project.unix_gid = None
        session.flush()
        gid, _ = _group(session, key=project.projcode)
        assert project.unix_gid == gid

    def test_project_with_gid_is_untouched(self, session):
        project = make_project(session)
        project.unix_gid = 94_000_000 + next_int('ldapsync_gid')
        session.flush()
        before = project.unix_gid
        _group(session, key=project.projcode)
        assert project.unix_gid == before

    def test_ncar_is_never_adhoc(self, session):
        gid, _ = _group(session, key='ncar')
        assert AdhocGroup.get_by_unix_gid(session, gid) is None
