"""Unit tests for sam.queries.disk_quota + sam.schemas.disk_quota.

Reproduces legacy ``GET /api/protected/admin/dasg/diskquota``. Factory-built
disk accounts give exact values for the transform (paths / quota / dataManager);
the schema test pins the legacy camelCase data_key contract.
"""
from datetime import datetime, timedelta

import pytest

from sam.projects.projects import ProjectDirectory
from sam.resources.resources import ResourceType
from sam.queries.disk_quota import get_disk_quotas
from sam.schemas import DiskQuotaSchema
from tests.factories import (
    make_account,
    make_allocation,
    make_disk_resource_root_directory,
    make_project,
    make_resource,
    make_resource_type,
    make_user,
)

pytestmark = pytest.mark.unit


def _resource_type(session, name):
    """Get-or-create a ResourceType (the snapshot DB already holds DISK/HPC)."""
    rt = session.query(ResourceType).filter_by(resource_type=name).first()
    return rt or make_resource_type(session, resource_type=name)


def _disk_account(session, *, root, amount=100.0, admin=None):
    """A DISK account with an active allocation under root directory `root`."""
    rt = _resource_type(session, 'DISK')
    resource = make_resource(session, resource_type=rt,
                             commission_date=datetime.now() - timedelta(days=30))
    make_disk_resource_root_directory(session, resource=resource, root_directory=root)
    project = make_project(session)
    if admin is not None:
        project.project_admin_user_id = admin.user_id
        session.flush()
    account = make_account(session, project=project, resource=resource)
    make_allocation(session, account=account, amount=amount)
    return project, resource, account


def _find(records, projcode):
    return next((r for r in records if r['projcode'] == projcode), None)


class TestGetDiskQuotas:

    def test_record_shape(self, session):
        project, resource, _ = _disk_account(session, root='/glade/p/dq_shape')
        rec = _find(get_disk_quotas(session), project.projcode)
        assert rec is not None
        assert rec['group_name'] == project.projcode.lower()
        assert rec['resource_name'] == resource.resource_name
        assert rec['quota'] == 100.0
        assert isinstance(rec['paths'], list)

    def test_paths_resolve_to_this_resource(self, session):
        project, resource, account = _disk_account(session, root='/glade/p/dq_paths')
        # a second disk resource owns a different root
        other = make_resource(session, resource_type=resource.resource_type,
                              commission_date=datetime.now() - timedelta(days=30))
        make_disk_resource_root_directory(session, resource=other,
                                          root_directory='/glade/p/dq_other')
        ProjectDirectory.create(session, project_id=project.project_id,
                                directory_name='/glade/p/dq_paths/sub')   # matches
        ProjectDirectory.create(session, project_id=project.project_id,
                                directory_name='/glade/p/dq_other/sub')   # other resource
        rec = _find(get_disk_quotas(session), project.projcode)
        assert rec['paths'] == ['/glade/p/dq_paths/sub']

    def test_data_manager_is_admin_when_set(self, session):
        admin = make_user(session)
        project, _, _ = _disk_account(session, root='/glade/p/dq_admin', admin=admin)
        rec = _find(get_disk_quotas(session), project.projcode)
        assert rec['data_manager'] == admin.username

    def test_data_manager_falls_back_to_lead(self, session):
        project, _, _ = _disk_account(session, root='/glade/p/dq_lead')
        rec = _find(get_disk_quotas(session), project.projcode)
        assert rec['data_manager'] == project.lead.username

    def test_non_disk_account_excluded(self, session):
        rt = _resource_type(session, 'HPC')
        resource = make_resource(session, resource_type=rt,
                                 commission_date=datetime.now() - timedelta(days=30))
        account = make_account(session, resource=resource)
        make_allocation(session, account=account, amount=50.0)
        projcode = account.project.projcode
        assert _find(get_disk_quotas(session), projcode) is None


class TestDiskQuotaSchema:

    def test_data_key_output_keys(self):
        record = {
            'projcode': 'ABCD0001', 'group_name': 'abcd0001',
            'data_manager': 'someone', 'resource_name': 'Campaign_Store',
            'quota': 123.0, 'paths': ['/glade/p/x'],
        }
        out = DiskQuotaSchema().dump(record)
        assert set(out.keys()) == {'projcode', 'groupName', 'dataManager',
                                   'resourceName', 'quota', 'paths'}
        assert out['groupName'] == 'abcd0001'
        assert out['dataManager'] == 'someone'
        assert out['resourceName'] == 'Campaign_Store'

    def test_quota_and_manager_nullable(self):
        out = DiskQuotaSchema().dump({
            'projcode': 'ABCD0002', 'group_name': 'abcd0002',
            'data_manager': None, 'resource_name': 'Stratus',
            'quota': None, 'paths': [],
        })
        assert out['quota'] is None
        assert out['dataManager'] is None
