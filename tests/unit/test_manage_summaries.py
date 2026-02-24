"""
Unit tests for charge summary management functions.

Tests the management layer (src/sam/manage/summaries.py):
- Shared private validation helpers (_resolve_*)
- upsert_comp_charge_summary()
- upsert_disk_charge_summary() / upsert_archive_charge_summary()
- Input schemas for API validation
"""

import pytest
from datetime import date

from sam.manage.summaries import (
    _resolve_user,
    _resolve_project,
    _resolve_resource,
    _resolve_account,
    _resolve_machine,
    _resolve_or_create_queue,
    upsert_comp_charge_summary,
    upsert_disk_charge_summary,
    upsert_archive_charge_summary,
)
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary
from sam.resources.machines import Queue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def derecho_resource(session):
    """Get the Derecho resource (HPC)."""
    from sam.resources.resources import Resource
    return Resource.get_by_name(session, 'Derecho')


@pytest.fixture
def derecho_account(session, test_project, derecho_resource):
    """Get the account linking SCSG0001 to Derecho."""
    from sam.accounting.accounts import Account
    return Account.get_by_project_and_resource(
        session, test_project.project_id, derecho_resource.resource_id
    )


@pytest.fixture
def derecho_machine(session, derecho_resource):
    """Get a machine on Derecho."""
    from sam.resources.machines import Machine
    return session.query(Machine).filter_by(
        resource_id=derecho_resource.resource_id
    ).first()


@pytest.fixture
def derecho_queue(session, derecho_resource):
    """Get a queue on Derecho."""
    return session.query(Queue).filter_by(
        resource_id=derecho_resource.resource_id
    ).first()


@pytest.fixture
def comp_kwargs(test_user, test_project, derecho_machine, derecho_queue):
    """Standard kwargs for upsert_comp_charge_summary."""
    return dict(
        activity_date=date(2098, 6, 15),
        act_username=test_user.username,
        act_projcode=test_project.projcode,
        act_unix_uid=test_user.unix_uid,
        resource_name='Derecho',
        machine_name=derecho_machine.name,
        queue_name=derecho_queue.queue_name,
        num_jobs=10,
        core_hours=1234.5,
        charges=987.65,
    )


@pytest.fixture
def storage_resource(session):
    """Get a storage resource (Campaign Store or similar)."""
    from sam.resources.resources import Resource, ResourceType
    # Find a DISK-type resource
    disk_type = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if disk_type:
        res = session.query(Resource).filter_by(resource_type_id=disk_type.resource_type_id).first()
        if res:
            return res
    # Fallback: try by name
    for name in ['Campaign Store', 'Glade']:
        res = Resource.get_by_name(session, name)
        if res:
            return res
    pytest.skip("No storage resource available in test database")


@pytest.fixture
def archive_resource(session):
    """Get an archive resource (HPSS or similar)."""
    from sam.resources.resources import Resource, ResourceType
    archive_type = session.query(ResourceType).filter_by(resource_type='ARCHIVE').first()
    if archive_type:
        res = session.query(Resource).filter_by(resource_type_id=archive_type.resource_type_id).first()
        if res:
            return res
    for name in ['HPSS', 'Quasar']:
        res = Resource.get_by_name(session, name)
        if res:
            return res
    pytest.skip("No archive resource available in test database")


# ---------------------------------------------------------------------------
# TestResolverHelpers
# ---------------------------------------------------------------------------

class TestResolverHelpers:
    """Tests for the shared private validation helpers."""

    def test_resolve_user_by_username(self, session, test_user):
        """Happy path: resolve user by username."""
        user = _resolve_user(session, test_user.username, test_user.unix_uid)
        assert user.user_id == test_user.user_id

    def test_resolve_user_by_uid_fallback(self, session, test_user):
        """Falls back to uid when username not found."""
        user = _resolve_user(session, 'nonexistent_user_xyz', test_user.unix_uid)
        assert user.user_id == test_user.user_id

    def test_resolve_user_not_found(self, session):
        """Raises ValueError containing both username and uid."""
        with pytest.raises(ValueError, match="User"):
            _resolve_user(session, 'nonexistent_user_xyz', 999999999)

    def test_resolve_user_none_uid(self, session):
        """act_unix_uid=None uses username only; error message contains 'no uid'."""
        with pytest.raises(ValueError, match="no uid"):
            _resolve_user(session, 'nonexistent_user_xyz', None)

    def test_resolve_project_not_found(self, session):
        """Raises ValueError containing projcode."""
        with pytest.raises(ValueError, match="ZZZZ9999"):
            _resolve_project(session, 'ZZZZ9999')

    def test_resolve_resource_not_found(self, session):
        """Raises ValueError containing resource name."""
        with pytest.raises(ValueError, match="FakeResource"):
            _resolve_resource(session, 'FakeResource')

    def test_resolve_account_not_found(self, session):
        """Raises ValueError containing project + resource info."""
        from sam.projects.projects import Project
        from sam.resources.resources import Resource
        project = Project.get_by_projcode(session, 'SCSG0001')
        # Find a resource this project does NOT have an account for
        all_resources = session.query(Resource).all()
        from sam.accounting.accounts import Account
        project_resource_ids = {
            a.resource_id for a in
            session.query(Account).filter_by(project_id=project.project_id).all()
        }
        for res in all_resources:
            if res.resource_id not in project_resource_ids:
                with pytest.raises(ValueError, match="No account"):
                    _resolve_account(session, project, res)
                return
        pytest.skip("All resources have accounts for this project")

    def test_resolve_machine_not_found(self, session, derecho_resource):
        """Raises ValueError containing machine + resource."""
        with pytest.raises(ValueError, match="Machine"):
            _resolve_machine(session, 'fake_machine_xyz', derecho_resource)

    def test_resolve_queue_not_found_no_create(self, session, derecho_resource):
        """Raises ValueError with hint about flag."""
        with pytest.raises(ValueError, match="create_queue_if_missing"):
            _resolve_or_create_queue(session, 'fake_queue_xyz', derecho_resource, False)

    def test_resolve_queue_creates_when_flag_set(self, session, derecho_resource):
        """New Queue row in session after flush."""
        queue = _resolve_or_create_queue(
            session, 'auto_test_queue', derecho_resource, True
        )
        assert queue.queue_id is not None
        assert queue.queue_name == 'auto_test_queue'
        assert queue.resource_id == derecho_resource.resource_id
        # Verify it's in the session
        found = session.query(Queue).filter_by(
            queue_name='auto_test_queue',
            resource_id=derecho_resource.resource_id,
        ).first()
        assert found is not None


# ---------------------------------------------------------------------------
# TestUpsertCompChargeSummary
# ---------------------------------------------------------------------------

class TestUpsertCompChargeSummary:
    """Tests for upsert_comp_charge_summary."""

    def test_insert_new(self, session, comp_kwargs):
        """Valid inputs -> row created, returns 'created'."""
        record, action = upsert_comp_charge_summary(session, **comp_kwargs)
        assert action == 'created'
        assert record.charge_summary_id is not None
        assert record.activity_date == comp_kwargs['activity_date']
        assert record.charges == comp_kwargs['charges']

    def test_update_existing(self, session, comp_kwargs):
        """Same natural key -> row updated, returns 'updated'."""
        record1, action1 = upsert_comp_charge_summary(session, **comp_kwargs)
        assert action1 == 'created'

        # Update with different charges
        updated_kwargs = dict(comp_kwargs, charges=555.55, num_jobs=20)
        record2, action2 = upsert_comp_charge_summary(session, **updated_kwargs)
        assert action2 == 'updated'
        assert record2.charge_summary_id == record1.charge_summary_id
        assert record2.charges == 555.55
        assert record2.num_jobs == 20

    def test_act_fields_immutable_on_update(self, session, comp_kwargs):
        """act_username, act_projcode, act_unix_uid unchanged after update."""
        record, _ = upsert_comp_charge_summary(session, **comp_kwargs)
        orig_act_username = record.act_username
        orig_act_projcode = record.act_projcode
        orig_act_unix_uid = record.act_unix_uid

        # Update with different charges
        updated_kwargs = dict(comp_kwargs, charges=111.11)
        record2, action = upsert_comp_charge_summary(session, **updated_kwargs)
        assert action == 'updated'
        assert record2.act_username == orig_act_username
        assert record2.act_projcode == orig_act_projcode
        assert record2.act_unix_uid == orig_act_unix_uid

    def test_resolved_fields_default_from_act(self, session, comp_kwargs):
        """Omitted username/projcode default to act_ values."""
        record, _ = upsert_comp_charge_summary(session, **comp_kwargs)
        assert record.username == comp_kwargs['act_username']
        assert record.projcode == comp_kwargs['act_projcode']
        assert record.unix_uid == comp_kwargs['act_unix_uid']

    def test_zero_unix_uid_preserved(self, session, comp_kwargs):
        """unix_uid=0 stored as 0, not replaced by act_unix_uid."""
        kwargs = dict(comp_kwargs, unix_uid=0)
        record, _ = upsert_comp_charge_summary(session, **kwargs)
        assert record.unix_uid == 0

    def test_put_semantics_nulls_optional_fields(self, session, comp_kwargs):
        """Second call omitting cos overwrites stored value with NULL."""
        kwargs_with_cos = dict(comp_kwargs, cos=5)
        record, _ = upsert_comp_charge_summary(session, **kwargs_with_cos)
        assert record.cos == 5

        # Update without cos -> should be NULL
        record2, action = upsert_comp_charge_summary(session, **comp_kwargs)
        assert action == 'updated'
        assert record2.cos is None

    def test_facility_name_override(self, session, comp_kwargs):
        """Explicit facility_name in payload stored, bypassing heuristic."""
        kwargs = dict(comp_kwargs, facility_name='TEST_FACILITY')
        record, _ = upsert_comp_charge_summary(session, **kwargs)
        assert record.facility_name == 'TEST_FACILITY'

    def test_invalid_user_raises(self, session, comp_kwargs):
        """Unknown username + uid -> ValueError containing 'User'."""
        kwargs = dict(comp_kwargs, act_username='nobody_xyz', act_unix_uid=999999999)
        with pytest.raises(ValueError, match="User"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_project_raises(self, session, comp_kwargs):
        """Unknown projcode -> ValueError containing 'Project'."""
        kwargs = dict(comp_kwargs, act_projcode='ZZZZ9999')
        with pytest.raises(ValueError, match="Project"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_resource_raises(self, session, comp_kwargs):
        """Unknown resource -> ValueError containing 'Resource'."""
        kwargs = dict(comp_kwargs, resource_name='FakeResource')
        with pytest.raises(ValueError, match="Resource"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_machine_raises(self, session, comp_kwargs):
        """Unknown machine -> ValueError containing 'Machine'."""
        kwargs = dict(comp_kwargs, machine_name='fake_machine_xyz')
        with pytest.raises(ValueError, match="Machine"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_missing_queue_no_flag_raises(self, session, comp_kwargs):
        """Missing queue, flag=False -> ValueError with hint."""
        kwargs = dict(comp_kwargs, queue_name='fake_queue_xyz')
        with pytest.raises(ValueError, match="create_queue_if_missing"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_create_queue_if_missing(self, session, comp_kwargs):
        """Missing queue, flag=True -> Queue created, insert succeeds."""
        kwargs = dict(comp_kwargs, queue_name='auto_test_queue_comp', create_queue_if_missing=True)
        record, action = upsert_comp_charge_summary(session, **kwargs)
        assert action == 'created'
        assert record.queue == 'auto_test_queue_comp'
        assert record.queue_id is not None

    def test_no_session_commit(self, session, comp_kwargs):
        """After call, session not committed (rollback cleans up)."""
        record, _ = upsert_comp_charge_summary(session, **comp_kwargs)
        record_id = record.charge_summary_id
        # Rollback should remove the record
        session.rollback()
        found = session.get(CompChargeSummary, record_id)
        assert found is None


# ---------------------------------------------------------------------------
# TestUpsertStorageChargeSummaries
# ---------------------------------------------------------------------------

STORAGE_CASES = [
    pytest.param(DiskChargeSummary, upsert_disk_charge_summary, 'disk', id='disk'),
    pytest.param(ArchiveChargeSummary, upsert_archive_charge_summary, 'archive', id='archive'),
]


class TestUpsertStorageChargeSummaries:
    """Tests for upsert_disk_charge_summary and upsert_archive_charge_summary."""

    def _get_storage_kwargs(self, session, test_user, test_project, resource_type):
        """Build kwargs for a storage upsert call."""
        from sam.resources.resources import Resource, ResourceType
        from sam.accounting.accounts import Account

        rt = session.query(ResourceType).filter_by(resource_type=resource_type).first()
        if not rt:
            pytest.skip(f"No {resource_type} resource type in database")

        resource = session.query(Resource).filter_by(resource_type_id=rt.resource_type_id).first()
        if not resource:
            pytest.skip(f"No {resource_type} resource in database")

        account = Account.get_by_project_and_resource(
            session, test_project.project_id, resource.resource_id
        )
        if not account:
            pytest.skip(f"No account for {test_project.projcode} on {resource.resource_name}")

        return dict(
            activity_date=date(2098, 6, 15),
            act_username=test_user.username,
            act_projcode=test_project.projcode,
            act_unix_uid=test_user.unix_uid,
            resource_name=resource.resource_name,
            charges=25.0,
            number_of_files=500,
            bytes=1024 * 1024 * 1024,
            terabyte_years=0.5,
        )

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_insert_new(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """Happy path insert."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        record, action = upsert_fn(session, **kwargs)
        assert action == 'created'
        assert record.charges == 25.0

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_update_existing(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """Natural key hit -> update."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        record1, action1 = upsert_fn(session, **kwargs)
        assert action1 == 'created'

        updated = dict(kwargs, charges=50.0)
        record2, action2 = upsert_fn(session, **updated)
        assert action2 == 'updated'
        assert record2.charges == 50.0

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_act_fields_immutable(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """act_ unchanged after update."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        record, _ = upsert_fn(session, **kwargs)
        orig = (record.act_username, record.act_projcode, record.act_unix_uid)

        updated = dict(kwargs, charges=99.9)
        record2, _ = upsert_fn(session, **updated)
        assert (record2.act_username, record2.act_projcode, record2.act_unix_uid) == orig

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_invalid_user(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """Unknown user -> ValueError."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        kwargs['act_username'] = 'nobody_xyz'
        kwargs['act_unix_uid'] = 999999999
        with pytest.raises(ValueError, match="User"):
            upsert_fn(session, **kwargs)

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_invalid_project(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """Unknown project -> ValueError."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        kwargs['act_projcode'] = 'ZZZZ9999'
        with pytest.raises(ValueError, match="Project"):
            upsert_fn(session, **kwargs)

    @pytest.mark.parametrize('model_cls, upsert_fn, res_type', STORAGE_CASES)
    def test_invalid_resource(self, session, test_user, test_project, model_cls, upsert_fn, res_type):
        """Unknown resource -> ValueError."""
        kwargs = self._get_storage_kwargs(session, test_user, test_project,
                                          'DISK' if res_type == 'disk' else 'ARCHIVE')
        kwargs['resource_name'] = 'FakeResource'
        with pytest.raises(ValueError, match="Resource"):
            upsert_fn(session, **kwargs)


# ---------------------------------------------------------------------------
# TestInputSchemas
# ---------------------------------------------------------------------------

class TestInputSchemas:
    """Tests for marshmallow input validation schemas."""

    def test_comp_valid_input(self):
        """All fields -> clean dict."""
        from sam.schemas.charges import CompChargeSummaryInputSchema
        schema = CompChargeSummaryInputSchema()
        data = schema.load({
            'activity_date': '2025-01-15',
            'act_username': 'benkirk',
            'act_projcode': 'SCSG0001',
            'act_unix_uid': 12345,
            'resource_name': 'Derecho',
            'machine_name': 'derecho',
            'queue_name': 'main',
            'num_jobs': 10,
            'core_hours': 1234.5,
            'charges': 987.65,
        })
        assert data['activity_date'] == date(2025, 1, 15)
        assert data['act_username'] == 'benkirk'
        assert data['num_jobs'] == 10

    def test_comp_missing_required(self):
        """Missing activity_date -> ValidationError."""
        from marshmallow import ValidationError
        from sam.schemas.charges import CompChargeSummaryInputSchema
        schema = CompChargeSummaryInputSchema()
        with pytest.raises(ValidationError) as exc_info:
            schema.load({
                'act_username': 'benkirk',
                'act_projcode': 'SCSG0001',
                'resource_name': 'Derecho',
                'machine_name': 'derecho',
                'queue_name': 'main',
                'num_jobs': 10,
                'core_hours': 100.0,
                'charges': 50.0,
            })
        assert 'activity_date' in exc_info.value.messages

    def test_comp_invalid_date(self):
        """Bad date string -> ValidationError."""
        from marshmallow import ValidationError
        from sam.schemas.charges import CompChargeSummaryInputSchema
        schema = CompChargeSummaryInputSchema()
        with pytest.raises(ValidationError):
            schema.load({
                'activity_date': 'not-a-date',
                'act_username': 'benkirk',
                'act_projcode': 'SCSG0001',
                'resource_name': 'Derecho',
                'machine_name': 'derecho',
                'queue_name': 'main',
                'num_jobs': 10,
                'core_hours': 100.0,
                'charges': 50.0,
            })

    def test_comp_negative_num_jobs(self):
        """num_jobs=-1 -> ValidationError."""
        from marshmallow import ValidationError
        from sam.schemas.charges import CompChargeSummaryInputSchema
        schema = CompChargeSummaryInputSchema()
        with pytest.raises(ValidationError):
            schema.load({
                'activity_date': '2025-01-15',
                'act_username': 'benkirk',
                'act_projcode': 'SCSG0001',
                'resource_name': 'Derecho',
                'machine_name': 'derecho',
                'queue_name': 'main',
                'num_jobs': -1,
                'core_hours': 100.0,
                'charges': 50.0,
            })

    def test_comp_queue_flag_defaults_false(self):
        """Absent flag -> False."""
        from sam.schemas.charges import CompChargeSummaryInputSchema
        schema = CompChargeSummaryInputSchema()
        data = schema.load({
            'activity_date': '2025-01-15',
            'act_username': 'benkirk',
            'act_projcode': 'SCSG0001',
            'resource_name': 'Derecho',
            'machine_name': 'derecho',
            'queue_name': 'main',
            'num_jobs': 10,
            'core_hours': 100.0,
            'charges': 50.0,
        })
        assert data['create_queue_if_missing'] is False

    def test_disk_valid_input(self):
        """All fields -> clean dict."""
        from sam.schemas.charges import DiskChargeSummaryInputSchema
        schema = DiskChargeSummaryInputSchema()
        data = schema.load({
            'activity_date': '2025-01-15',
            'act_username': 'benkirk',
            'act_projcode': 'SCSG0001',
            'resource_name': 'Campaign Store',
            'charges': 25.0,
            'number_of_files': 500,
            'bytes': 1073741824,
            'terabyte_years': 0.5,
        })
        assert data['charges'] == 25.0
        assert data['number_of_files'] == 500

    def test_archive_valid_input(self):
        """All fields -> clean dict."""
        from sam.schemas.charges import ArchiveChargeSummaryInputSchema
        schema = ArchiveChargeSummaryInputSchema()
        data = schema.load({
            'activity_date': '2025-01-15',
            'act_username': 'benkirk',
            'act_projcode': 'SCSG0001',
            'resource_name': 'HPSS',
            'charges': 60.0,
            'number_of_files': 1000,
            'terabyte_years': 1.2,
        })
        assert data['charges'] == 60.0

    def test_storage_negative_bytes(self):
        """bytes=-1 -> ValidationError."""
        from marshmallow import ValidationError
        from sam.schemas.charges import DiskChargeSummaryInputSchema
        schema = DiskChargeSummaryInputSchema()
        with pytest.raises(ValidationError):
            schema.load({
                'activity_date': '2025-01-15',
                'act_username': 'benkirk',
                'act_projcode': 'SCSG0001',
                'resource_name': 'Campaign Store',
                'charges': 25.0,
                'bytes': -1,
            })

    def test_storage_missing_required(self):
        """Missing charges -> ValidationError."""
        from marshmallow import ValidationError
        from sam.schemas.charges import ArchiveChargeSummaryInputSchema
        schema = ArchiveChargeSummaryInputSchema()
        with pytest.raises(ValidationError) as exc_info:
            schema.load({
                'activity_date': '2025-01-15',
                'act_username': 'benkirk',
                'act_projcode': 'SCSG0001',
                'resource_name': 'HPSS',
            })
        assert 'charges' in exc_info.value.messages
