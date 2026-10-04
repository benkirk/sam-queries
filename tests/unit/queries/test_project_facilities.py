"""project_facilities / project_panels: one query mapping many projcodes to facility and panel."""

from sam.queries.projects import project_facilities, project_panels
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


def test_project_panels_adds_the_panel_and_agrees_with_facilities(session):
    univ = make_project(session, facility_name='UNIV')
    panels = project_panels(session, [univ.projcode])
    fid, facility, panel = panels[univ.projcode]
    assert panel == univ.allocation_type.panel.panel_name
    assert project_facilities(session, [univ.projcode]) == {univ.projcode: (fid, facility)}
