"""The sweep: SAM's side in bulk, one compare per key, counts with capped keys; ``limit``
narrows SAM to the sample so a smoke counts no absences."""
import os

from factories import (make_adhoc_group, make_adhoc_system_account_entry, make_project,
                       make_user, next_int)
from factories.directory import FakeDirectory, make_group_record, make_person_record
from sam.queries.directory_sweep import MAX_IDS, SWEPT_KINDS, sweep

_WORKER = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or '0')


def _gid():
    return 77_700_000 + _WORKER * 10_000 + next_int('sweep_gid')


def _project(session):
    lead = make_user(session, upid=True)
    project = make_project(session, lead=lead)
    project.unix_gid = _gid()
    session.flush()
    return project, lead


class TestGroups:
    def test_absent_and_unknown_groups_are_counted(self, session):
        present, lead = _project(session)
        absent, _ = _project(session)
        fake = FakeDirectory()
        fake.add(make_group_record(gid=present.unix_gid, name=present.projcode,
                                   tags=('exclude-from-google',), member_upids=(lead.upid,),
                                   managers=(lead.upid,)))
        fake.add(make_group_record(gid=_gid(), name='unknown-to-sam'))
        report = sweep(session, fake, kinds=('group',))
        r = report.kinds['group']
        assert (r.directory, r.compared) == (2, 1) and r.sam >= 2
        assert r.counts['missing_in_sam'] == 1 and r.ids['missing_in_sam'] == ('unknown-to-sam',)
        assert r.counts['missing_in_directory'] >= 1
        assert present.projcode not in r.ids.get('missing_in_directory', ())
        assert len(r.ids['missing_in_directory']) <= MAX_IDS
        assert fake.calls == [('records', 'group')]
        assert report.headline().startswith('group: 2 read,') and 'missing in directory' in report.headline()
        assert report.as_dict()['kinds']['group']['counts']['missing_in_sam'] == 1

    def test_limit_narrows_sam_to_the_sample(self, session):
        present, lead = _project(session)
        fake = FakeDirectory()
        fake.add(make_group_record(gid=present.unix_gid, name=present.projcode,
                                   tags=('exclude-from-google',), member_upids=(lead.upid,),
                                   managers=(lead.upid,)))
        r = sweep(session, fake, kinds=('group',), limit=1).kinds['group']
        assert (r.directory, r.sam, r.compared, r.counts) == (1, 1, 1, {})
        assert r.line() == f'group: 1 read, 1 in SAM, 0 drift'

    def test_an_adhoc_group_compares_members_not_managers(self, session):
        person = make_user(session, upid=True)
        group = make_adhoc_group(session)
        make_adhoc_system_account_entry(session, group, person.username)
        fake = FakeDirectory()
        fake.add(make_group_record(gid=group.unix_gid, name=group.group_name, tags=('hpc',),
                                   member_upids=(person.upid, 999_999_999), managers=(person.upid,)))
        r = sweep(session, fake, kinds=('group',), limit=1).kinds['group']
        assert r.counts == {'member_missing_in_sam': 1}
        assert r.ids['member_missing_in_sam'] == (group.group_name,)


class TestPeople:
    def test_people_by_upid(self, session):
        user = make_user(session, upid=True, first_name='Jane', last_name='Doe', token_type='DUO')
        fake = FakeDirectory()
        fake.add(make_person_record(upid=user.upid, username=user.username, unix_uid=user.unix_uid,
                                    first_name='Someone', emails=(), phones=(), positions=()))
        fake.add(make_person_record(upid=999_999_997, username='nobody', emails=(), phones=()))
        r = sweep(session, fake, kinds=('person',), limit=2).kinds['person']
        assert (r.directory, r.sam, r.compared) == (2, 1, 1)
        assert r.counts['name'] == 1 and r.ids['name'] == (user.username,)
        assert r.counts['missing_in_sam'] == 1 and r.ids['missing_in_sam'] == ('nobody',)


def test_every_kind_has_a_sam_side(session):
    """An empty directory under a limit: each kind's SAM query runs and narrows to nothing."""
    report = sweep(session, FakeDirectory(), limit=1)
    assert tuple(report.kinds) == SWEPT_KINDS
    assert all((r.directory, r.sam, r.counts) == (0, 0, {}) for r in report.kinds.values())
