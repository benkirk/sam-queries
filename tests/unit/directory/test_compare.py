"""One test per category, and the fragment that reaches the log."""
from datetime import datetime

import pytest

from factories.directory import (make_collaboration_record, make_person_record,
                                 make_position_record)
from sam.integration.directory import (EmailRecord, GroupRecord, InstitutionRecord,
                                       OrganizationRecord, PhoneRecord)
from sam.integration.directory.compare import (
    AFFILIATION_CATEGORIES, GROUP_CATEGORIES, INSTITUTION_CATEGORIES, ORGANIZATION_CATEGORIES,
    PERSON_CATEGORIES, AffiliationSnapshot, DriftReport, GroupSnapshot, InstitutionSnapshot,
    OrganizationSnapshot, PersonSnapshot, compare_group, compare_institution,
    compare_organization, compare_person,
)

NOW = datetime(2026, 10, 15, 12, 0, 0)
UPID = 35399


def snapshot(**overrides) -> PersonSnapshot:
    values = dict(upid=UPID, username=f'user{UPID}', unix_uid=10000 + UPID, active=True,
                  first_name='Jane', last_name='Doe', token_type='duo',
                  emails=(EmailRecord(f'user{UPID}@example.invalid', True),),
                  phones=(PhoneRecord('ucar office', '(303) 555-0100'),), user_id=99)
    values.update(overrides)
    return PersonSnapshot(**values)


def categories(report: DriftReport):
    return sorted(d.category for d in report.discrepancies)


def one(report: DriftReport, category: str):
    found = [d for d in report.discrepancies if d.category == category]
    assert len(found) == 1, categories(report)
    return found[0]


class TestPersonFields:
    def test_an_identical_person_has_no_drift(self):
        report = compare_person(make_person_record(), snapshot(), key='k', side='sam', now=NOW)
        assert report.discrepancies == ()
        assert report.fragment() == 'drift=0'

    def test_missing_on_either_side(self):
        absent = compare_person(None, snapshot(), key='k', side='sam', now=NOW)
        assert absent.found_in_directory is False
        assert categories(absent) == ['missing_in_directory']
        assert absent.fragment() == 'absent'
        unknown = compare_person(make_person_record(), None, key='k', side='sam', now=NOW)
        assert categories(unknown) == ['missing_in_sam']
        assert unknown.ids() == [f'person:{UPID}']

    def test_username_differs_case_insensitively_and_is_valued(self):
        report = compare_person(make_person_record(username='USER35399'), snapshot(),
                                key='k', side='sam', now=NOW)
        assert report.discrepancies == ()
        d = one(compare_person(make_person_record(username='other'), snapshot(),
                               key='k', side='sam', now=NOW), 'username')
        assert (d.sam_value, d.directory_value, d.sam_id) == (f'user{UPID}', 'other', 99)

    def test_unix_uid_active_and_token_type(self):
        report = compare_person(make_person_record(unix_uid=1, active=False, token_type='CRYPTO'),
                                snapshot(), key='k', side='sam', now=NOW)
        assert categories(report) == ['active', 'token_type', 'unix_uid']
        assert one(report, 'active').directory_value == 'false'

    def test_academic_status_mapped_and_unmapped(self):
        mapped = compare_person(make_person_record(academic_status='GS', academic_status_code='GS'),
                                snapshot(), key='k', side='sam', now=NOW)
        assert one(mapped, 'academic_status').directory_value == 'GS'
        unmapped = compare_person(make_person_record(academic_status='Dignitary'), snapshot(),
                                  key='k', side='sam', now=NOW)
        assert categories(unmapped) == ['academic_status_unmapped']
        same = compare_person(make_person_record(academic_status='gs', academic_status_code='GS'),
                              snapshot(academic_status_code='GS'), key='k', side='sam', now=NOW)
        assert same.discrepancies == ()

    def test_names_are_counted_per_field_and_never_valued(self):
        report = compare_person(make_person_record(first_name='Janet', nickname='Jan'),
                                snapshot(), key='k', side='sam', now=NOW)
        assert [(d.category, d.field) for d in report.discrepancies] == [
            ('name', 'first_name'), ('name', 'nickname')]
        assert all(d.sam_value == d.directory_value == '' for d in report.discrepancies)
        assert 'Jan' not in report.fragment()

    def test_emails_missing_and_primary_without_the_address(self):
        record = make_person_record(emails=(EmailRecord('new@example.invalid', True),
                                            EmailRecord(f'user{UPID}@example.invalid', False)))
        report = compare_person(record, snapshot(), key='k', side='sam', now=NOW)
        assert sorted(d.field for d in report.discrepancies) == ['missing', 'primary']
        assert '@' not in report.fragment()

    def test_phones_compare_on_label_and_digits(self):
        same = compare_person(make_person_record(phones=(PhoneRecord('Ucar Office', '303.555.0100'),)),
                              snapshot(), key='k', side='sam', now=NOW)
        assert same.discrepancies == ()
        report = compare_person(make_person_record(phones=(PhoneRecord('Cell', '303-555-0199'),)),
                                snapshot(), key='k', side='sam', now=NOW)
        assert sorted(d.field for d in report.discrepancies) == ['extra', 'missing']


class TestPositions:
    def sam_row(self, **overrides):
        values = dict(row_id=11, employer_id=375, start=datetime(2022, 1, 18),
                      end=None, directory_id='P001')
        values.update(overrides)
        return AffiliationSnapshot(**values)

    def test_a_matched_identical_row_is_quiet(self):
        report = compare_person(make_person_record(positions=(make_position_record(),)),
                                snapshot(positions=(self.sam_row(),)), key='k', side='sam', now=NOW)
        assert report.discrepancies == ()

    def test_planned_end(self):
        record = make_person_record(positions=(make_position_record(end=datetime(2027, 1, 13)),))
        d = one(compare_person(record, snapshot(positions=(self.sam_row(),)),
                               key='k', side='sam', now=NOW), 'planned_end')
        assert (d.sam_id, d.directory_id, d.directory_value) == (11, 'P001', '2027-01-13')

    def test_finished_vs_open_both_ways(self):
        finished = make_person_record(positions=(make_position_record(active=False, end=datetime(2024, 7, 25)),))
        d = one(compare_person(finished, snapshot(positions=(self.sam_row(),)),
                               key='k', side='sam', now=NOW), 'finished_vs_open')
        assert (d.sam_value, d.directory_value) == ('open', 'finished')
        current = make_person_record(positions=(make_position_record(),))
        d = one(compare_person(current, snapshot(positions=(self.sam_row(end=datetime(2024, 1, 1, 23, 59, 59)),)),
                               key='k', side='sam', now=NOW), 'finished_vs_open')
        assert (d.sam_value, d.directory_value) == ('finished', 'open')

    def test_an_inactive_flag_with_no_end_is_finished(self):
        """D-C: the normal shape of a finished directory position."""
        record = make_person_record(positions=(make_position_record(active=False),))
        report = compare_person(record, snapshot(positions=(self.sam_row(),)),
                                key='k', side='sam', now=NOW)
        assert categories(report) == ['finished_vs_open']

    def test_end_day_compares_by_mountain_day_so_a_23_59_59_end_matches_midnight(self):
        record = make_person_record(positions=(make_position_record(active=False, end=datetime(2024, 7, 25)),))
        same = compare_person(record, snapshot(positions=(self.sam_row(end=datetime(2024, 7, 25, 23, 59, 59)),)),
                              key='k', side='sam', now=NOW)
        assert same.discrepancies == ()
        d = one(compare_person(record, snapshot(positions=(self.sam_row(end=datetime(2024, 7, 24, 23, 59, 59)),)),
                               key='k', side='sam', now=NOW), 'end_day')
        assert (d.sam_value, d.directory_value) == ('2024-07-24', '2024-07-25')

    def test_start_day_catches_the_workday_winter_shift(self):
        d = one(compare_person(make_person_record(positions=(make_position_record(),)),
                               snapshot(positions=(self.sam_row(start=datetime(2022, 1, 17, 23)),)),
                               key='k', side='sam', now=NOW), 'start_day')
        assert (d.sam_value, d.directory_value) == ('2022-01-17', '2022-01-18')

    def test_employer_differs_only_when_the_directory_names_one(self):
        d = one(compare_person(make_person_record(positions=(make_position_record(organization_id=9),)),
                               snapshot(positions=(self.sam_row(),)), key='k', side='sam', now=NOW),
                'employer')
        assert (d.sam_value, d.directory_value) == ('375', '9')
        quiet = compare_person(make_person_record(positions=(make_position_record(organization_id=None),)),
                               snapshot(positions=(self.sam_row(),)), key='k', side='sam', now=NOW)
        assert quiet.discrepancies == ()

    def test_no_sam_row_versus_twin(self):
        lone = make_person_record(positions=(make_position_record('P002'),))
        d = one(compare_person(lone, snapshot(), key='k', side='sam', now=NOW), 'no_sam_row')
        assert d.directory_id == 'P002' and d.sam_id is None
        twins = make_person_record(positions=(make_position_record('P001'),
                                              make_position_record('P002', source='PDB')))
        report = compare_person(twins, snapshot(positions=(self.sam_row(),)),
                                key='k', side='sam', now=NOW)
        assert categories(report) == ['twin']
        assert one(report, 'twin').directory_value == 'P001'

    def test_history_open_for_rows_the_directory_lacks(self):
        rows = (self.sam_row(row_id=1, directory_id=''),
                self.sam_row(row_id=2, directory_id='gone'),
                self.sam_row(row_id=3, directory_id='', end=datetime(2020, 1, 1)))
        report = compare_person(make_person_record(), snapshot(positions=rows),
                                key='k', side='sam', now=NOW)
        assert [(d.sam_id, d.field) for d in report.discrepancies] == [
            (1, 'no directory id'), (2, 'id not in directory')]


class TestCollaborations:
    def sam_row(self, **overrides):
        values = dict(row_id=21, employer_id=1, start=datetime(2020, 7, 1), end=None)
        values.update(overrides)
        return AffiliationSnapshot(**values)

    def test_quiet_when_current_on_both_sides(self):
        report = compare_person(make_person_record(collaborations=(make_collaboration_record(),)),
                                snapshot(collaborations=(self.sam_row(),)),
                                key='k', side='sam', now=NOW)
        assert report.discrepancies == ()

    def test_the_latest_row_for_the_institution_is_the_one_compared(self):
        rows = (self.sam_row(row_id=1, start=datetime(2010, 1, 1), end=datetime(2012, 1, 1)),
                self.sam_row(row_id=2))
        report = compare_person(make_person_record(collaborations=(make_collaboration_record(),)),
                                snapshot(collaborations=rows), key='k', side='sam', now=NOW)
        assert report.discrepancies == ()
        finished = make_collaboration_record(active=False, end=datetime(2025, 1, 1))
        d = one(compare_person(make_person_record(collaborations=(finished,)),
                               snapshot(collaborations=rows), key='k', side='sam', now=NOW),
                'finished_vs_open')
        assert d.sam_id == 2

    def test_no_sam_row_no_directory_start_and_history(self):
        record = make_person_record(collaborations=(make_collaboration_record(7, start=None),))
        report = compare_person(record, snapshot(collaborations=(self.sam_row(),)),
                                key='k', side='sam', now=NOW)
        assert categories(report) == ['history_open', 'no_directory_start', 'no_sam_row']
        assert one(report, 'history_open').sam_id == 21

    def test_a_planned_end_is_reported_for_a_collaboration_too(self):
        record = make_person_record(collaborations=(make_collaboration_record(end=datetime(2027, 1, 1)),))
        report = compare_person(record, snapshot(collaborations=(self.sam_row(),)),
                                key='k', side='sam', now=NOW)
        assert categories(report) == ['planned_end']


class TestFragment:
    def test_counts_ids_and_a_cap(self):
        rows = tuple(AffiliationSnapshot(i, 375, datetime(2022, 1, 1), None, '') for i in range(1, 14))
        report = compare_person(make_person_record(), snapshot(positions=rows),
                                key='k', side='sam', now=NOW)
        text = report.fragment()
        assert text.startswith('drift=13[history_open:13] ids=uo:1,uo:2,')
        assert text.endswith('uo:10...')
        assert report.counts() == {'history_open': 13}

    def test_every_category_is_declared(self):
        assert set(PERSON_CATEGORIES) & set(AFFILIATION_CATEGORIES) == set()
        for group in (PERSON_CATEGORIES, AFFILIATION_CATEGORIES, ORGANIZATION_CATEGORIES,
                      INSTITUTION_CATEGORIES, GROUP_CATEGORIES):
            assert len(group) == len(set(group))


class TestOrganizationsInstitutionsGroups:
    def test_organization(self):
        record = OrganizationRecord(375, 'HSS', 'High-End Services Section', 'Division', '0400', 32, True)
        same = OrganizationSnapshot(375, 'HSS', 'High-End Services Section', 'Division', '0400', 32, True)
        assert compare_organization(record, same, key='375', side='sam').discrepancies == ()
        report = compare_organization(record, OrganizationSnapshot(375, 'HSS', 'Old', 'Division', '0400', None, False),
                                      key='375', side='sam')
        assert categories(report) == ['active', 'name', 'parent']
        assert compare_organization(None, same, key='375', side='sam').fragment() == 'absent'
        assert categories(compare_organization(record, None, key='375', side='sam')) == ['missing_in_sam']

    def test_institution_state_equal_to_country_means_no_state(self):
        record = InstitutionRecord(1, 'U', 'University', country='US', state_code='US',
                                   org_type='University', nsf_org_code='1', city='C')
        snap = InstitutionSnapshot(1, 'U', 'University', country='US', state_code='',
                                   org_type='university', nsf_org_code='1', city='C')
        assert compare_institution(record, snap, key='1', side='sam').discrepancies == ()
        moved = InstitutionRecord(1, 'U', 'University', country='US', state_code='AK',
                                  org_type='Government', nsf_org_code='2', city='C', street='1 Main')
        report = compare_institution(moved, snap, key='1', side='sam')
        assert categories(report) == ['address', 'nsf_org_code', 'state', 'type']
        assert one(report, 'state').directory_value == 'US/AK'

    def test_group_members_tags_and_flags(self):
        record = GroupRecord(1564, 'csg', ('casper', 'hpc'), (7, 35399), True)
        snap = GroupSnapshot(1564, 'csg', True, frozenset({'hpc'}), frozenset({35399, 8}))
        report = compare_group(record, snap, key='csg', side='sam')
        assert categories(report) == ['member_missing_in_directory', 'member_missing_in_sam']
        assert one(report, 'member_missing_in_sam').directory_id == '7'
        assert one(report, 'member_missing_in_directory').sam_value == '8'
        assert report.ids() == ['group:7']
        tagged = compare_group(record, GroupSnapshot(1564, 'csg', False, frozenset({'derecho'}),
                                                     frozenset({7, 35399})), key='csg', side='sam')
        assert categories(tagged) == ['active', 'tags']
        assert compare_group(None, snap, key='csg', side='sam').fragment() == 'absent'

    def test_group_managers_compare_only_when_sam_holds_them(self):
        record = GroupRecord(68283, 'SCSG0001', ('hpc',), (7, 35399), True, managers=(7, 35399))
        project = GroupSnapshot(68283, 'SCSG0001', True, frozenset({'hpc'}), frozenset({7, 35399}),
                                managers=frozenset({35399, 8}))
        report = compare_group(record, project, key='SCSG0001', side='sam')
        assert categories(report) == ['manager_missing_in_directory', 'manager_missing_in_sam']
        assert one(report, 'manager_missing_in_sam').directory_id == '7'
        assert one(report, 'manager_missing_in_directory').sam_value == '8'
        adhoc = GroupSnapshot(68283, 'SCSG0001', True, frozenset({'hpc'}), frozenset({7, 35399}))
        assert compare_group(record, adhoc, key='SCSG0001', side='sam').discrepancies == ()
