"""The UCAR adapter's parsers, fed ldap3's attribute shape; no ldap3 and no socket."""
from datetime import datetime

import pytest

from factories.directory import entry
from sam.dates import from_generalized_time
from sam.integration.directory.config import DirectoryConfig
from sam.integration.directory.ucar import (ACADEMIC_STATUS_LABELS, UCARDirectory,
                                            academic_status_code, parse_collaboration,
                                            parse_group, parse_institution,
                                            parse_organization, parse_person,
                                            parse_position, split_person_entries)

UCAR = {'x-ucar-upid': 35399, 'uid': 'benkirk', 'uidNumber': 38057, 'givenName': 'Benjamin',
        'sn': 'Kirk', 'x-ucar-preferredName': 'Ben', 'x-ucar-middleName': 'Shelton',
        'mail': 'Ben@example.invalid', 'x-ucar-forwardEmail': 'ben@example.invalid',
        'x-ucar-typedPhone': ['External Office: 303-555-0100', '5550199'],
        'x-ucar-authType': 'DUO', 'x-ucar-active': 'TRUE', 'x-ucar-source': 'FI',
        'physicalDeliveryOfficeName': 'ML-55B', 'x-ucar-supervisor': 'x-ucar-upid=1,ou=allPeople',
        'x-ucar-adminFor': ['gidNumber=1564,ou=allGroups,dc=ucar,dc=edu', 'cn=other'],
        'objectClass': 'x-ucar-uniquePerson', 'modifyTimestamp': '20260804085819Z'}


class TestGeneralizedTime:
    @pytest.mark.parametrize('value,expected', [
        ('20260715235959Z', datetime(2026, 7, 15, 17, 59, 59)),
        ('20260115060000Z', datetime(2026, 1, 14, 23, 0, 0)),
        ('20260715235959.123Z', datetime(2026, 7, 15, 17, 59, 59)),
        ('20260101120000+0000', datetime(2026, 1, 1, 5, 0, 0)),
        ('20150216030415-0700', datetime(2015, 2, 16, 3, 4, 15)),
        ('20220118000000-0600', datetime(2022, 1, 18, 0, 0, 0)),
        ('20240725152424-0600', datetime(2024, 7, 25, 15, 24, 24)),
    ])
    def test_z_converts_and_a_mountain_offset_is_wall_time(self, value, expected):
        """WARNING: Workday writes midnight -0600 year-round; the January case must stay 01-18."""
        assert from_generalized_time(value) == expected

    @pytest.mark.parametrize('value', ['', None, 'garbage', '2026071523', '20261340000000Z'])
    def test_empty_or_malformed_is_none(self, value):
        assert from_generalized_time(value) is None


class TestAcademicStatus:
    def test_a_code_passes_through_upper_cased(self):
        assert academic_status_code('gs') == 'GS'

    @pytest.mark.parametrize('label,code', [('Graduate Student', 'GS'), ('Post Doc', 'PD'),
                                            ('University Research Staff', 'UR'),
                                            ('UNKNOWN', 'UK')])
    def test_a_label_maps_case_insensitively(self, label, code):
        assert academic_status_code(label) == code

    def test_an_unmapped_label_is_none_and_blank_is_none(self):
        assert academic_status_code('Visiting Dignitary') is None
        assert academic_status_code('') is None

    def test_every_mapped_code_is_two_letters_or_fewer(self):
        assert all(len(code) <= 2 for code in ACADEMIC_STATUS_LABELS.values())


class TestPerson:
    def test_the_fields(self):
        person = parse_person(entry(**UCAR))
        assert (person.upid, person.username, person.unix_uid, person.active) == (
            35399, 'benkirk', 38057, True)
        assert (person.first_name, person.last_name, person.nickname, person.middle_name,
                person.name_suffix) == ('Benjamin', 'Kirk', 'Ben', 'Shelton', '')
        assert person.token_type == 'DUO' and person.source == 'FI'
        assert person.modified == datetime(2026, 8, 4, 2, 58, 19)

    def test_mail_is_primary_and_a_same_forward_address_is_folded(self):
        person = parse_person(entry(**UCAR))
        assert [(e.address, e.primary) for e in person.emails] == [('Ben@example.invalid', True)]
        other = parse_person(entry(**{**UCAR, 'x-ucar-forwardEmail': 'x@example.invalid'}))
        assert [e.primary for e in other.emails] == [True, False]

    def test_typed_phones_split_on_the_first_colon_and_a_bare_number_keeps_no_label(self):
        person = parse_person(entry(**UCAR))
        assert [(p.label, p.number) for p in person.phones] == [
            ('External Office', '303-555-0100'), ('', '5550199')]

    def test_academic_status_carries_the_raw_value_and_the_code(self):
        person = parse_person(entry(**{**UCAR, 'x-ucar-academicStatus': 'Graduate Student'}))
        assert (person.academic_status, person.academic_status_code) == ('Graduate Student', 'GS')
        unmapped = parse_person(entry(**{**UCAR, 'x-ucar-academicStatus': 'Dignitary'}))
        assert (unmapped.academic_status, unmapped.academic_status_code) == ('Dignitary', None)

    def test_extra_is_wired_not_shown(self):
        person = parse_person(entry(**UCAR))
        assert person.extra['office'] == 'ML-55B'
        assert person.extra['admin_for'] == '1564'
        assert 'supervisor' in person.extra

    def test_no_upid_is_no_person(self):
        assert parse_person(entry(uid='x')) is None

    def test_children_are_split_by_object_class(self):
        entries = [
            ('x-ucar-upid=35399,ou=allPeople', entry(**UCAR)),
            ('x-ucar-positionId=P1,x-ucar-upid=35399,ou=allPeople', entry(
                objectClass='x-ucar-position', attrs={'x-ucar-positionId': 'P1',
                                                      'x-ucar-organizationId': '375',
                                                      'x-ucar-startDate': '20220118000000-0600',
                                                      'x-ucar-active': 'TRUE'})),
            ('x-ucar-externalOrgId=86,x-ucar-upid=35399,ou=allPeople', entry(
                objectClass='x-ucar-collaboration', attrs={'x-ucar-externalOrgId': '86',
                                                           'x-ucar-active': 'FALSE',
                                                           'x-ucar-endDate': '20150216030415-0700',
                                                           'x-ucar-adminOrg': 'CISL'})),
        ]
        person_attrs, positions, collaborations = split_person_entries(entries)
        person = parse_person(person_attrs, positions, collaborations)
        assert [(p.position_id, p.organization_id, p.start, p.end, p.active)
                for p in person.positions] == [('P1', 375, datetime(2022, 1, 18), None, True)]
        assert [(c.institution_id, c.end, c.active, c.admin_org) for c in person.collaborations] == [
            (86, datetime(2015, 2, 16, 3, 4, 15), False, 'CISL')]


class TestOtherRecords:
    def test_position_without_an_organization(self):
        p = parse_position(entry(attrs={'x-ucar-positionId': '5713', 'x-ucar-active': 'FALSE',
                                        'modifiersName': 'cn=peercred'}))
        assert (p.position_id, p.organization_id, p.active, p.modifier) == (
            '5713', None, False, 'cn=peercred')

    def test_a_collaboration_needs_an_institution(self):
        assert parse_collaboration(entry(attrs={'x-ucar-active': 'TRUE'})) is None

    def test_organization_parent_under_external_orgs_is_none(self):
        base = {'x-ucar-organizationId': '375', 'ou': 'HSS', 'o': 'High-End Services Section',
                'x-ucar-orgLevel': 'Division', 'x-ucar-orgLevelCode': '0400',
                'x-ucar-active': 'TRUE'}
        org = parse_organization(entry(attrs={**base, 'x-ucar-parent':
                                              'x-ucar-organizationId=32,ou=allOrganizations,dc=ucar,dc=edu'}))
        assert (org.organization_id, org.acronym, org.name, org.level_code, org.parent_org_id,
                org.active) == (375, 'HSS', 'High-End Services Section', '0400', 32, True)
        external = parse_organization(entry(attrs={**base, 'x-ucar-parent':
                                                   'x-ucar-externalOrgId=9,ou=externalOrgs,dc=ucar,dc=edu'}))
        assert external.parent_org_id is None
        assert parse_organization(entry(ou='x')) is None

    def test_institution(self):
        inst = parse_institution(entry(attrs={
            'x-ucar-externalOrgId': '1', 'x-ucar-shortOrgName': 'U OF ALASKA FAIRBANKS',
            'o': 'UNIVERSITY OF ALASKA FAIRBANKS', 'c': 'US', 'st': 'Alaska',
            'x-ucar-stateProvCode': 'AK', 'l': 'FAIRBANKS', 'postalCode': '99775-0800',
            'x-ucar-nsfOrgCode': '0290940', 'x-ucar-externalOrgType': 'University'}))
        assert (inst.institution_id, inst.acronym, inst.country, inst.state_code, inst.city,
                inst.postal_code, inst.nsf_org_code, inst.org_type, inst.street) == (
            1, 'U OF ALASKA FAIRBANKS', 'US', 'AK', 'FAIRBANKS', '99775-0800', '0290940',
            'University', '')
        assert parse_institution(entry(o='x')) is None

    def test_group_members_by_upid_and_managers_by_gid(self):
        group = parse_group(entry(attrs={
            'gidNumber': '1564', 'x-ucar-groupName': 'csg', 'cn': 'csg',
            'x-ucar-tag': ['hpc', 'derecho'], 'x-ucar-active': 'TRUE',
            'uniqueMember': ['x-ucar-upid=35399,ou=allPeople,dc=ucar,dc=edu',
                             'x-ucar-upid=7,ou=allPeople,dc=ucar,dc=edu',
                             'uid=svc,ou=serviceAccounts,dc=ucar,dc=edu',
                             'gidNumber=99,ou=allGroups,dc=ucar,dc=edu'],
            'x-ucar-adminFor': ['gidNumber=75040,ou=allGroups,dc=ucar,dc=edu']}))
        assert (group.gid, group.name, group.tags, group.member_upids, group.active,
                group.manager_gids) == (1564, 'csg', ('derecho', 'hpc'), (7, 35399), True, (75040,))
        assert parse_group(entry(cn='x')) is None


class _Conn:
    """A connection whose ``search`` answers from a queue; ``calls`` records base, filter, scope."""
    def __init__(self, answers):
        self.answers = [[{'type': 'searchResEntry', 'dn': dn, 'attributes': attrs} for dn, attrs in a]
                        for a in answers]
        self.calls = []
        self.response = []

    def search(self, base, flt, **kw):
        self.calls.append((base, flt, kw.get('search_scope')))
        self.response = self.answers.pop(0)


class TestAdapterWithoutASocket:
    def test_group_reads_its_managers_from_the_people_side(self):
        """One connection, two searches: the group, then the people whose adminFor names it."""
        reader = UCARDirectory(DirectoryConfig(provider='ucar', url='ldap://ldap.example.invalid'))
        group = entry(attrs={'gidNumber': '68283', 'x-ucar-groupName': 'SCSG0001', 'cn': 'scsg0001',
                             'x-ucar-active': 'TRUE', 'x-ucar-source': 'PDB',
                             'uniqueMember': ['x-ucar-upid=35399,ou=allPeople,dc=ucar,dc=edu']})
        people = [('x-ucar-upid=7,ou=allPeople,dc=ucar,dc=edu', entry(attrs={'x-ucar-upid': '7'})),
                  ('x-ucar-upid=35399,ou=allPeople,dc=ucar,dc=edu', entry(attrs={'x-ucar-upid': '35399'}))]
        reader._shared = conn = _Conn([[('gidNumber=68283,ou=allGroups,dc=ucar,dc=edu', group)], people])
        record = reader.group(68283)
        assert (record.name, record.member_upids, record.managers) == ('SCSG0001', (35399,), (7, 35399))
        assert [c[0] for c in conn.calls] == ['ou=allGroups,dc=ucar,dc=edu', 'ou=allPeople,dc=ucar,dc=edu']
        assert conn.calls[1][1] == '(x-ucar-adminFor=gidNumber=68283,ou=allGroups,dc=ucar,dc=edu)'
        reader._shared = _Conn([[]])
        assert reader.group(1) is None

    def test_an_empty_url_is_not_configured_and_refuses(self):
        from sam.integration.directory import DirectoryNotConfigured
        reader = UCARDirectory(DirectoryConfig(provider='ucar', url=''))
        assert reader.configured is False
        with pytest.raises(DirectoryNotConfigured):
            reader.person(1)

    def test_a_failed_connection_is_unavailable_and_check_says_so(self):
        """ldap3 refuses the address before any socket opens."""
        from sam.integration.directory import DirectoryUnavailable
        reader = UCARDirectory(DirectoryConfig(provider='ucar', url='ldap://nonexistent.invalid',
                                               timeout=1))
        with pytest.raises(DirectoryUnavailable):
            reader.person(1)
        ok, message = reader.check()
        assert ok is False and message

    def test_search_rejects_an_unknown_kind_before_connecting(self):
        reader = UCARDirectory(DirectoryConfig(provider='ucar', url='ldap://ldap.example.invalid'))
        with pytest.raises(ValueError):
            reader.search('planet', 'x')
        assert reader.search('person', '   ') == []

    def test_the_summary_is_the_config_plus_the_name(self):
        reader = UCARDirectory(DirectoryConfig(provider='ucar', url='ldap://ldap.example.invalid'))
        assert reader.summary()['provider'] == 'ucar'
        assert reader.summary()['configured'] is True
