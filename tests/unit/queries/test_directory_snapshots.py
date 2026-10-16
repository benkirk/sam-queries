"""ORM rows and the daemon's increment reduce to the same snapshot; the check reads once."""
import logging
import os
from datetime import datetime

import pytest

from factories import (make_email_address, make_institution, make_organization, make_phone,
                       make_user, make_user_institution, make_user_organization, next_int)
from factories.directory import FakeDirectory, make_person_record, make_position_record
from sam.integration.directory import DirectoryUnavailable, OrganizationRecord, outage
from sam.queries.directory import (check_organization, check_person, run_check,
                                   snapshot_from_increment, snapshot_from_organization,
                                   snapshot_from_user)
from sam.schemas.forms.ldapsync import OrganizationSyncInput, UserSyncInput

NOW = datetime(2026, 10, 15, 12, 0, 0)
_WORKER = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or '0')


def _upid():
    """Unique across xdist workers: ``users.upid`` carries a unique index."""
    return 80_000_000 + _WORKER * 100_000 + next_int('directory_upid')


@pytest.fixture
def user(session):
    u = make_user(session, first_name='Jane', last_name='Doe', upid=_upid(), token_type='DUO')
    make_email_address(session, u, email=f'{u.username}@example.invalid', is_primary=True)
    make_phone(session, u, number='303-555-0100', phone_type='Ucar Office')
    org = make_organization(session)
    make_user_organization(session, user=u, organization=org, start_date=datetime(2022, 1, 18),
                           idms_unique_name='P001')
    make_user_institution(session, user=u, institution=make_institution(session),
                          start_date=datetime(2020, 7, 1))
    session.refresh(u)
    return u


class TestSnapshots:
    def test_from_user(self, user):
        snap = snapshot_from_user(user)
        assert (snap.upid, snap.username, snap.unix_uid, snap.active, snap.user_id) == (
            user.upid, user.username, user.unix_uid, True, user.user_id)
        assert [(e.address, e.primary) for e in snap.emails] == [(f'{user.username}@example.invalid', True)]
        assert [(p.label, p.number) for p in snap.phones] == [('Ucar Office', '303-555-0100')]
        assert [(p.employer_id, p.directory_id, p.start) for p in snap.positions] == [
            (user.organizations[0].organization_id, 'P001', datetime(2022, 1, 18))]
        assert [c.employer_id for c in snap.collaborations] == [user.institutions[0].institution_id]
        assert snap.academic_status_code is None and snap.token_type == 'DUO'

    def test_a_pending_deactivation_reads_as_inactive(self, user):
        user.deactivate = NOW
        assert snapshot_from_user(user).active is False

    def test_from_increment(self):
        data = UserSyncInput().load({
            'userName': 'jdoe', 'unixUid': 5, 'upid': 9, 'active': True, 'locked': False,
            'chargingExempt': False, 'firstname': 'Jane', 'lastname': 'Doe',
            'academicStatus': 'Graduate Student', 'tokenType': 'DUO',
            'emails': [{'email': 'j@example.invalid', 'primary': True}],
            'phones': [{'phoneNumber': '5550100', 'extPhoneType': 'Cell'}],
            'positions': [{'positionId': 11, 'organizationId': 375, 'startDate': 1642489200000,
                           'endDate': None, 'idmsUniqueName': 'P001'}],
            'collaborations': [{'collaborationId': 21, 'institutionId': 1,
                                'startDate': 1593583200000, 'endDate': None}]})
        snap = snapshot_from_increment(data)
        assert (snap.upid, snap.username, snap.academic_status_code, snap.token_type) == (
            9, 'jdoe', 'GS', 'DUO')
        assert [(p.row_id, p.employer_id, p.directory_id, p.start) for p in snap.positions] == [
            (11, 375, 'P001', datetime(2022, 1, 18))]
        assert [(c.row_id, c.employer_id) for c in snap.collaborations] == [(21, 1)]
        assert [(p.label, p.number) for p in snap.phones] == [('Cell', '5550100')]

    def test_from_organization(self, session):
        org = make_organization(session)
        snap = snapshot_from_organization(org)
        assert (snap.organization_id, snap.acronym, snap.active) == (
            org.organization_id, org.acronym, bool(org.active))


class TestChecks:
    def _increment(self, user, **overrides):
        body = {'userName': user.username, 'unixUid': user.unix_uid, 'upid': user.upid,
                'active': True, 'locked': False, 'chargingExempt': False,
                'firstname': 'Jane', 'lastname': 'Doe', 'tokenType': 'DUO',
                'emails': [{'email': f'{user.username}@example.invalid', 'primary': True}],
                'phones': [{'phoneNumber': '303-555-0100', 'extPhoneType': 'Ucar Office'}],
                'positions': [{'positionId': user.organizations[0].user_organization_id,
                               'organizationId': user.organizations[0].organization_id,
                               'startDate': 1642489200000, 'endDate': None,
                               'idmsUniqueName': 'P001'}],
                'collaborations': [{'collaborationId': user.institutions[0].user_institution_id,
                                    'institutionId': user.institutions[0].institution_id,
                                    'startDate': 1593583200000, 'endDate': None}]}
        body.update(overrides)
        return UserSyncInput().load(body)

    def _record(self, user, **overrides):
        values = dict(
            upid=user.upid, username=user.username, unix_uid=user.unix_uid,
            emails=(), phones=(),
            positions=(make_position_record('P001', organization_id=user.organizations[0].organization_id),),
            collaborations=())
        values.update(overrides)
        return make_person_record(**values)

    def test_both_sides_from_one_read(self, session, user):
        fake = FakeDirectory()
        fake.add(self._record(user, emails=make_person_record().emails,
                              phones=make_person_record().phones))
        increment, sam = check_person(session, fake, self._increment(user), now=NOW)
        assert fake.calls == [('person', user.upid)]
        assert (increment.side, sam.side) == ('increment', 'sam')
        assert increment.key == sam.key == user.username
        # the directory's address is missing and primary differs; the collaboration is history
        assert sam.counts() == {'email': 2, 'history_open': 1}
        assert increment.counts() == {'email': 2, 'history_open': 1}

    def test_a_role_login_without_a_upid_is_skipped(self, session):
        fake = FakeDirectory()
        data = UserSyncInput().load({'userName': 'svc', 'unixUid': 1, 'active': True,
                                     'locked': False, 'chargingExempt': False})
        assert check_person(session, fake, data, now=NOW) is None
        assert fake.calls == []

    def test_an_organization_check(self, session):
        org = make_organization(session)
        fake = FakeDirectory()
        fake.add(OrganizationRecord(org.organization_id, org.acronym, 'Renamed', '', '', None, True))
        data = OrganizationSyncInput().load({'organizationId': org.organization_id,
                                             'name': org.name, 'acronym': org.acronym,
                                             'active': True})
        increment, sam = check_organization(session, fake, data)
        assert sam.counts() == {'name': 1}
        assert increment.counts() == {'name': 1}


class TestRunCheck:
    def test_logs_one_line_with_the_username_and_ids_only(self, session, user, caplog):
        fake = FakeDirectory()
        fake.add(make_person_record(upid=user.upid, username=user.username,
                                    unix_uid=user.unix_uid, emails=(), phones=(),
                                    first_name='Janet'))
        with caplog.at_level(logging.INFO, logger='sam.queries.directory'):
            reports = run_check(session, 'person', TestChecks()._increment(user), user.unix_uid,
                                reader=fake, now=NOW)
        assert reports is not None
        line = next(r.message for r in caplog.records if 'directory check' in r.message)
        assert line.startswith(f'ldapsync: directory check person {user.username} key={user.unix_uid} found=True')
        assert 'name:1' in line and 'Janet' not in line and '@' not in line

    def test_unconfigured_reader_and_unknown_kind_do_nothing(self, session, user):
        fake = FakeDirectory(configured=False)
        assert run_check(session, 'person', TestChecks()._increment(user), 1, reader=fake) is None
        assert run_check(session, 'gid', {}, 1, reader=FakeDirectory()) is None
        assert fake.calls == []

    def test_the_suite_default_reader_is_the_null_reader(self, session, user):
        assert run_check(session, 'person', TestChecks()._increment(user), 1) is None

    def test_an_unavailable_directory_sets_the_latch(self, session, user, caplog, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(outage.time, 'monotonic', lambda: clock[0])
        outage.reset()
        fake = FakeDirectory()
        fake.raise_with = DirectoryUnavailable('timeout')
        data = TestChecks()._increment(user)
        with caplog.at_level(logging.WARNING, logger='sam.queries.directory'):
            assert run_check(session, 'person', data, 1, reader=fake) is None
        assert outage.is_down() is True
        assert 'checks paused' in caplog.text
        fake.raise_with = None
        assert run_check(session, 'person', data, 1, reader=fake) is None
        assert fake.calls == [('person', user.upid)]
        outage.reset()
