"""Tests for FacilityResource write methods — the per-(facility, resource)
fair-share override backing the Admin > Resources override UI.

The override row is what the fstree query prefers over the facility default
via ``COALESCE(fr.fair_share_percentage, f.fair_share_percentage)``. These
tests cover the model-level create/update/delete/upsert/clear helpers; the
end-to-end "unset restores the facility default" behavior through the query
lives in test_fstree_queries.py::TestFacilityResourceOverride.
"""
import pytest

from sam.resources.facilities import FacilityResource

from factories import make_facility, make_resource


pytestmark = pytest.mark.unit


class TestFacilityResourceCreate:

    def test_create_sets_fields(self, session):
        fac = make_facility(session, fair_share_percentage=31.0)
        res = make_resource(session)
        fr = FacilityResource.create(
            session,
            facility_id=fac.facility_id,
            resource_id=res.resource_id,
            fair_share_percentage=12.5,
        )
        assert fr.facility_resource_id is not None
        assert fr.facility_id == fac.facility_id
        assert fr.resource_id == res.resource_id
        assert fr.fair_share_percentage == 12.5
        session.rollback()

    def test_create_rejects_out_of_range(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        with pytest.raises(ValueError, match="between 0 and 100"):
            FacilityResource.create(
                session,
                facility_id=fac.facility_id,
                resource_id=res.resource_id,
                fair_share_percentage=150.0,
            )
        session.rollback()


class TestFacilityResourceUpdate:

    def test_update_changes_value(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        fr = FacilityResource.create(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=10.0,
        )
        fr.update(fair_share_percentage=42.0)
        assert fr.fair_share_percentage == 42.0
        session.rollback()

    def test_update_rejects_out_of_range(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        fr = FacilityResource.create(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=10.0,
        )
        with pytest.raises(ValueError, match="between 0 and 100"):
            fr.update(fair_share_percentage=-1.0)
        session.rollback()


class TestGetOverride:

    def test_returns_none_when_absent(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        assert FacilityResource.get_override(session, fac.facility_id, res.resource_id) is None
        session.rollback()

    def test_returns_row_when_present(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        fr = FacilityResource.create(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=5.0,
        )
        found = FacilityResource.get_override(session, fac.facility_id, res.resource_id)
        assert found is fr
        session.rollback()


class TestSetOverride:

    def test_creates_when_absent(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        fr = FacilityResource.set_override(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=7.5,
        )
        assert fr.fair_share_percentage == 7.5
        assert FacilityResource.get_override(session, fac.facility_id, res.resource_id) is fr
        session.rollback()

    def test_updates_when_present(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        first = FacilityResource.set_override(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=7.5,
        )
        second = FacilityResource.set_override(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=9.0,
        )
        # Upsert: same row, updated value — no duplicate created.
        assert second.facility_resource_id == first.facility_resource_id
        assert second.fair_share_percentage == 9.0
        rows = (
            session.query(FacilityResource)
            .filter_by(facility_id=fac.facility_id, resource_id=res.resource_id)
            .count()
        )
        assert rows == 1
        session.rollback()


class TestClearOverride:

    def test_deletes_existing(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        FacilityResource.create(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
            fair_share_percentage=3.0,
        )
        removed = FacilityResource.clear_override(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
        )
        assert removed is True
        assert FacilityResource.get_override(session, fac.facility_id, res.resource_id) is None
        session.rollback()

    def test_noop_when_absent(self, session):
        fac = make_facility(session)
        res = make_resource(session)
        removed = FacilityResource.clear_override(
            session, facility_id=fac.facility_id, resource_id=res.resource_id,
        )
        assert removed is False
        session.rollback()
