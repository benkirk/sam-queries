"""Per kind: lever off withholds everything; lever on writes exactly the table's rows; dry run writes nothing."""
import os
from datetime import datetime

import pytest
from sqlalchemy import event

from factories import (make_email_address, make_institution, make_organization, make_phone,
                       make_user, make_user_institution, make_user_organization, next_int)
from factories.directory import (FakeDirectory, make_collaboration_record,
                                 make_person_record, make_position_record)
from sam.core.users import AcademicStatus, EmailAddress
from sam.integration.directory import (EmailRecord, InstitutionRecord, OrganizationRecord,
                                       PhoneRecord)
from sam.integration.directory.config import DirectoryConfig
from sam.manage.directory import (ReconcileResult, reconcile_from_directory,
                                  reconcile_organization, reconcile_person,
                                  reconcile_institution)

NOW = datetime(2026, 10, 15, 12, 0, 0)
_WORKER = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or '0')
OFF = DirectoryConfig(provider='fake', url='x')


def levers(*kinds):
    return DirectoryConfig(provider='fake', url='x', apply_kinds=frozenset(kinds))


def _upid():
    return 81_000_000 + _WORKER * 100_000 + next_int('directory_apply_upid')


@pytest.fixture
def writes(session):
    seen = []

    def _listen(conn, cursor, statement, *args):
        if statement.lstrip().split(' ', 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE'):
            seen.append(statement)
    engine = session.get_bind()
    event.listen(engine, 'before_cursor_execute', _listen)
    yield seen
    event.remove(engine, 'before_cursor_execute', _listen)


@pytest.fixture
def user(session):
    u = make_user(session, first_name='Jane', last_name='Doe', upid=_upid(), token_type='DUO')
    make_email_address(session, u, email=f'{u.username}@example.invalid', is_primary=True)
    make_phone(session, u, number='303-555-0100', phone_type='Ucar Office')
    session.refresh(u)
    return u


def record_for(user, **overrides):
    values = dict(upid=user.upid, username=user.username, unix_uid=user.unix_uid,
                  emails=(EmailRecord(f'{user.username}@example.invalid', True),),
                  phones=(PhoneRecord('Ucar Office', '303-555-0100'),))
    values.update(overrides)
    return make_person_record(**values)


def categories(items):
    return sorted(i.category for i in items)


class TestPersonFields:
    def test_identical_is_a_no_op(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user), config=levers('person'), now=NOW)
        assert (result.applied, result.withheld, writes) == ((), (), [])
        assert result.fragment() == 'applied=0 withheld=0'

    def test_lever_off_withholds_and_writes_nothing(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user, first_name='Janet', token_type='CRYPTO'),
                                  config=OFF, now=NOW)
        assert categories(result.withheld) == ['name', 'token_type'] and result.applied == ()
        assert writes == [] and user.first_name == 'Jane'

    def test_names_token_type_and_academic_status(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user, first_name='Janet', nickname='Jan',
                                                            token_type='CRYPTO', academic_status='GS',
                                                            academic_status_code='GS'),
                                  config=levers('person'), now=NOW)
        assert categories(result.applied) == ['academic_status', 'name', 'name', 'token_type']
        assert (user.first_name, user.nickname, user.token_type) == ('Janet', 'Jan', 'CRYPTO')
        assert user.academic_status_id == session.query(AcademicStatus).filter_by(
            academic_status_code='GS').first().academic_status_id
        assert len(writes) == 1
        assert 'Janet' not in result.fragment()

    def test_an_unmapped_label_and_the_identity_columns_are_withheld(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user, academic_status='Dignitary',
                                                            username='other', unix_uid=1),
                                  config=levers('person'), now=NOW)
        assert categories(result.withheld) == ['academic_status_unmapped', 'unix_uid', 'username']
        assert writes == [] and user.username != 'other'

    def test_emails_are_added_and_primary_follows_mail(self, session, user, writes):
        record = record_for(user, emails=(EmailRecord('new@example.invalid', True),
                                          EmailRecord(f'{user.username}@example.invalid', False)))
        result = reconcile_person(session, user, record, config=levers('person'), now=NOW)
        assert categories(result.applied) == ['email', 'email']
        rows = {e.email_address: e.is_primary for e in user.email_addresses}
        assert rows == {'new@example.invalid': True, f'{user.username}@example.invalid': False}
        assert session.query(EmailAddress).filter_by(user_id=user.user_id).count() == 2

    def test_emails_mirror_the_directory_and_sam_only_ones_go(self, session, user, writes):
        make_email_address(session, user, email='stale@example.invalid', is_primary=False)
        session.refresh(user)
        record = record_for(user, emails=(EmailRecord('new@example.invalid', True),))
        result = reconcile_person(session, user, record, config=levers('person'), now=NOW)
        assert categories(result.applied) == ['email', 'email', 'email', 'email']
        assert [(e.email_address, e.is_primary) for e in user.email_addresses] == [
            ('new@example.invalid', True)]
        assert session.query(EmailAddress).filter_by(user_id=user.user_id).count() == 1

    def test_a_record_with_no_email_deletes_nothing(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user, emails=()),
                                  config=levers('person'), now=NOW)
        assert categories(result.withheld) == ['email'] and result.applied == ()
        assert writes == [] and len(user.email_addresses) == 1

    def test_phones_are_mirrored_and_an_unknown_label_withholds(self, session, user, writes):
        record = record_for(user, phones=(PhoneRecord('Cell', '303-555-0199'),))
        result = reconcile_person(session, user, record, config=levers('person'), now=NOW)
        assert categories(result.applied) == ['phone', 'phone']
        assert [(p.phone_type.phone_type, p.phone_number) for p in user.phones] == [('Cell', '303-555-0199')]
        odd = reconcile_person(session, user, record_for(user, phones=(PhoneRecord('Pager', '1'),)),
                               config=levers('person'), now=NOW)
        assert categories(odd.withheld) == ['phone'] and categories(odd.applied) == ['phone']

    def test_active_goes_through_the_sync_transition_and_fires_the_hook(self, session, user):
        user.active = False
        user.deactivate = datetime(2026, 1, 1)
        session.flush()
        fired = []
        result = reconcile_person(session, user, record_for(user, active=True), config=levers('person'),
                                  now=NOW, on_reactivate=lambda u, closed_at: fired.append(closed_at))
        assert categories(result.applied) == ['active']
        assert (user.active, user.deactivate, fired) == (True, None, [datetime(2026, 1, 1)])
        off = reconcile_person(session, user, record_for(user, active=False), config=levers('person'), now=NOW)
        assert categories(off.applied) == ['active'] and user.deactivate == NOW

    def test_dry_run_decides_and_touches_nothing(self, session, user, writes):
        result = reconcile_person(session, user, record_for(user, first_name='Janet'),
                                  config=levers('person'), now=NOW, dry_run=True)
        assert categories(result.applied) == ['name'] and result.dry_run is True
        assert result.fragment().startswith('dry-run applied=1')
        assert user.first_name == 'Jane' and writes == []

    def test_absent_on_either_side_applies_nothing(self, session, user):
        gone = reconcile_person(session, user, None, config=levers('person'), now=NOW)
        assert categories(gone.withheld) == ['missing_in_directory'] and gone.applied == ()
        new = reconcile_person(session, None, make_person_record(upid=_upid()), config=levers('person'), now=NOW)
        assert categories(new.withheld) == ['missing_in_sam'] and new.applied == ()


class TestPositions:
    @pytest.fixture
    def held(self, session, user):
        org = make_organization(session)
        row = make_user_organization(session, user=user, organization=org,
                                     start_date=datetime(2022, 1, 18), idms_unique_name='P001')
        session.refresh(user)
        return org, row

    def test_planned_end_and_employer_in_place(self, session, user, held, writes):
        org, row = held
        other = make_organization(session)
        before = len(writes)
        record = record_for(user, positions=(make_position_record(
            'P001', organization_id=other.organization_id, end=datetime(2027, 1, 13)),))
        result = reconcile_person(session, user, record, config=levers('position'), now=NOW)
        assert categories(result.applied) == ['employer', 'planned_end']
        assert (row.organization_id, row.end_date) == (other.organization_id, datetime(2027, 1, 13, 23, 59, 59))
        assert len(writes) - before == 1

    def test_the_person_lever_alone_withholds_positions(self, session, user, held, writes):
        org, row = held
        record = record_for(user, positions=(make_position_record(
            'P001', organization_id=org.organization_id, end=datetime(2027, 1, 13)),))
        result = reconcile_person(session, user, record, config=levers('person'), now=NOW)
        assert categories(result.withheld) == ['planned_end'] and writes == []

    def test_finished_with_an_end_closes_the_row_without_one_withholds(self, session, user, held):
        org, row = held
        dated = record_for(user, positions=(make_position_record(
            'P001', organization_id=org.organization_id, active=False, end=datetime(2024, 7, 25, 15)),))
        result = reconcile_person(session, user, dated, config=levers('position'), now=NOW)
        assert categories(result.applied) == ['finished_vs_open']
        assert row.end_date == datetime(2024, 7, 25, 23, 59, 59)
        row.end_date = None
        undated = record_for(user, positions=(make_position_record(
            'P001', organization_id=org.organization_id, active=False),))
        result = reconcile_person(session, user, undated, config=levers('position'), now=NOW)
        assert categories(result.withheld) == ['finished_vs_open'] and row.end_date is None

    def test_a_reopen_is_withheld(self, session, user, held):
        org, row = held
        row.end_date = datetime(2024, 1, 1, 23, 59, 59)
        record = record_for(user, positions=(make_position_record('P001', organization_id=org.organization_id),))
        result = reconcile_person(session, user, record, config=levers('position'), now=NOW)
        assert categories(result.withheld) == ['finished_vs_open'] and row.end_date is not None

    def test_a_new_row_for_a_known_organization_but_not_a_twin(self, session, user, held, writes):
        org, row = held
        record = record_for(user, positions=(
            make_position_record('P001', organization_id=org.organization_id),
            make_position_record('P002', organization_id=org.organization_id, source='PDB'),
            make_position_record('P003', organization_id=999_999_999),
            make_position_record('P004', organization_id=org.organization_id, start=None)))
        result = reconcile_person(session, user, record, config=levers('position'), now=NOW)
        # P003: unknown organization; P002 and P004: twins of the held P001
        assert categories(result.withheld) == ['no_sam_row', 'twin', 'twin']
        assert result.applied == ()
        lone = record_for(user, positions=(make_position_record('P001', organization_id=org.organization_id),
                                           make_position_record('P009', organization_id=make_organization(session).organization_id)))
        result = reconcile_person(session, user, lone, config=levers('position'), now=NOW)
        assert categories(result.applied) == ['no_sam_row']
        assert sorted(r.idms_unique_name for r in user.organizations) == ['P001', 'P009']

    def test_history_and_day_shifts_are_withheld(self, session, user, held):
        org, row = held
        make_user_organization(session, user=user, organization=org, start_date=datetime(2010, 1, 1))
        session.refresh(user)
        record = record_for(user, positions=(make_position_record(
            'P001', organization_id=org.organization_id, start=datetime(2022, 1, 19)),))
        result = reconcile_person(session, user, record, config=levers('position'), now=NOW)
        assert categories(result.withheld) == ['history_open', 'start_day'] and result.applied == ()


class TestCollaborations:
    def test_close_and_insert_only(self, session, user, writes):
        inst = make_institution(session)
        row = make_user_institution(session, user=user, institution=inst, start_date=datetime(2020, 7, 1))
        other = make_institution(session)
        session.refresh(user)
        record = record_for(user, collaborations=(
            make_collaboration_record(inst.institution_id, active=False, end=datetime(2025, 1, 1, 3)),
            make_collaboration_record(other.institution_id, start=datetime(2026, 1, 1), end=datetime(2027, 1, 1)),
            make_collaboration_record(999_999_999, start=datetime(2026, 1, 1))))
        result = reconcile_person(session, user, record, config=levers('collaboration'), now=NOW)
        assert categories(result.applied) == ['finished_vs_open', 'no_sam_row']
        assert categories(result.withheld) == ['no_sam_row']
        assert row.end_date == datetime(2025, 1, 1, 3)
        new = next(r for r in user.institutions if r.institution_id == other.institution_id)
        # the mixin floors a midnight end to 23:59:59 (AFFILIATIONS section 5, stored-as-sent pending)
        assert (new.start_date, new.end_date) == (datetime(2026, 1, 1), datetime(2027, 1, 1, 23, 59, 59))

    def test_a_planned_end_is_withheld_for_collaborations(self, session, user):
        inst = make_institution(session)
        row = make_user_institution(session, user=user, institution=inst, start_date=datetime(2020, 7, 1))
        session.refresh(user)
        record = record_for(user, collaborations=(make_collaboration_record(inst.institution_id, end=datetime(2027, 1, 1)),))
        result = reconcile_person(session, user, record, config=levers('collaboration'), now=NOW)
        assert categories(result.withheld) == ['planned_end'] and row.end_date is None


class TestOrganizationsAndInstitutions:
    def test_organization_fields_and_a_missing_parent(self, session, writes):
        org = make_organization(session)
        before = len(writes)
        record = OrganizationRecord(org.organization_id, org.acronym, 'Renamed', 'Lab', '0200', 999_999_999, False)
        result = reconcile_organization(session, org, record, config=levers('organization'))
        assert categories(result.applied) == ['active', 'level', 'level_code', 'name']
        assert categories(result.withheld) == ['parent']
        assert (org.name, org.level, org.level_code, org.active, org.parent_org_id) == (
            'Renamed', 'Lab', '0200', False, None)
        assert len(writes) - before == 1

    def test_a_directory_organization_sam_lacks_is_created(self, session):
        oid = 90_000_000 + _WORKER * 1000 + next_int('directory_new_org')
        record = OrganizationRecord(oid, 'NEWX', 'New Section', '', '', None, True)
        result = reconcile_organization(session, None, record, config=levers('organization'))
        assert categories(result.applied) == ['missing_in_sam']
        from sam.core.organizations import Organization
        assert session.get(Organization, oid).acronym == 'NEWX'
        withheld = reconcile_organization(session, None, record, config=OFF)
        assert withheld.applied == () and categories(withheld.withheld) == ['missing_in_sam']

    def test_institution_type_and_state_are_looked_up(self, session, writes):
        inst = make_institution(session)
        record = InstitutionRecord(inst.institution_id, inst.acronym, 'New Name', country='US',
                                   state_code='AK', org_type='University', nsf_org_code='7',
                                   city='FAIRBANKS', postal_code='99775')
        result = reconcile_institution(session, inst, record, config=levers('institution'))
        assert 'name' in categories(result.applied) and 'type' in categories(result.applied)
        session.refresh(inst)
        assert (inst.name, inst.nsf_org_code, inst.city, inst.zip) == ('New Name', '7', 'FAIRBANKS', '99775')
        assert inst.institution_type.type == 'University'
        assert inst.state_prov is not None and inst.state_prov.code == 'AK'
        odd = InstitutionRecord(inst.institution_id, inst.acronym, 'New Name', country='US',
                                state_code='AK', org_type='Lodge', nsf_org_code='7',
                                city='FAIRBANKS', postal_code='99775')
        result = reconcile_institution(session, inst, odd, config=levers('institution'))
        assert categories(result.withheld) == ['type']


class TestDispatch:
    def test_reconcile_from_directory_reads_once_and_dispatches(self, session, user):
        fake = FakeDirectory()
        fake.add(record_for(user, first_name='Janet'))
        result = reconcile_from_directory(session, 'person', user.upid, reader=fake,
                                          config=levers('person'), now=NOW)
        assert isinstance(result, ReconcileResult) and categories(result.applied) == ['name']
        assert fake.calls == [('person', user.upid)]
        assert reconcile_from_directory(session, 'gid', 1, reader=fake, config=OFF) is None
        assert reconcile_from_directory(session, 'person', None, reader=fake, config=OFF) is None

    def test_groups_are_reported_only(self, session):
        from sam.integration.directory import GroupRecord
        from sam.manage.directory import reconcile_group
        result = reconcile_group(session, 999_999_999, GroupRecord(999_999_999, 'ghost', (), (7,), True),
                                 config=levers('group'))
        assert categories(result.withheld) == ['missing_in_sam'] and result.applied == ()
