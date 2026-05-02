"""Tests for DiskResourceRootDirectory soft-delete (active flag) + create/update.

Phase 1 of the disk_resource_root_directory schema-parity work — exercises the
new `active` column (provided by ActiveFlagMixin) and the universal `is_active`
hybrid (CLAUDE.md §5).
"""
import pytest

from sam.resources.resources import DiskResourceRootDirectory
from tests.factories import make_disk_resource_root_directory


pytestmark = pytest.mark.unit


class TestDiskResourceRootDirectoryActive:

    def test_default_active_is_true(self, session):
        dr = make_disk_resource_root_directory(session)
        assert dr.active is True
        assert dr.is_active is True
        session.rollback()

    def test_create_inactive(self, session):
        dr = make_disk_resource_root_directory(session, active=False)
        assert dr.active is False
        assert dr.is_active is False
        session.rollback()

    def test_update_flips_active(self, session):
        dr = make_disk_resource_root_directory(session)
        dr.update(active=False)
        assert dr.is_active is False
        dr.update(active=True)
        assert dr.is_active is True
        session.rollback()

    def test_other_update_fields_preserve_active(self, session):
        dr = make_disk_resource_root_directory(session, active=False)
        dr.update(charging_exempt=True)
        assert dr.is_active is False  # active untouched when not passed
        assert dr.charging_exempt is True
        session.rollback()

    def test_is_active_sql_filter(self, session):
        active_dr = make_disk_resource_root_directory(session)
        inactive_dr = make_disk_resource_root_directory(session, active=False)

        active_ids = {
            r.root_directory_id
            for r in session.query(DiskResourceRootDirectory)
            .filter(DiskResourceRootDirectory.is_active)
            .all()
        }
        inactive_ids = {
            r.root_directory_id
            for r in session.query(DiskResourceRootDirectory)
            .filter(~DiskResourceRootDirectory.is_active)
            .all()
        }

        assert active_dr.root_directory_id in active_ids
        assert active_dr.root_directory_id not in inactive_ids
        assert inactive_dr.root_directory_id in inactive_ids
        assert inactive_dr.root_directory_id not in active_ids
        session.rollback()
