"""project_facilities: one query mapping many projcodes to their facility."""

from sam.queries.projects import project_facilities
from factories import make_project


def test_maps_each_projcode_to_its_facility(session):
    univ = make_project(session, facility_name='UNIV')
    wna = make_project(session, facility_name='WNA')
    got = project_facilities(session, [univ.projcode, wna.projcode])
    assert got[univ.projcode][1] == 'UNIV'
    assert got[wna.projcode][1] == 'WNA'
    assert got[univ.projcode][0] == univ.allocation_type.panel.facility.facility_id


def test_projects_without_a_facility_and_unknown_codes_are_absent(session):
    orphan = make_project(session)
    assert project_facilities(session, [orphan.projcode, 'NOSUCH99', None]) == {}


def test_empty_input(session):
    assert project_facilities(session, []) == {}
