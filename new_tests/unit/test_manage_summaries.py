"""Charge-summary management tests — Phase 3 port.

Ported from tests/unit/test_manage_summaries.py. The legacy file relied on
SCSG0001 + Derecho being present in the snapshot for every upsert test.
The port builds a self-contained graph per test via factories:

  fresh_user → fresh_project → fresh_resource (HPC/DISK/ARCHIVE)
  → fresh_account → fresh_machine → fresh_queue

This isolates each test from the snapshot — the resolver helpers
(`_resolve_user`, `_resolve_project`, etc.) just query the session so
they see the factory-flushed rows the same as snapshot rows.

Tests that depended on snapshot-specific structure (e.g. "find a
single-machine resource") are rewritten to construct the structure
directly via factories.
"""
from datetime import date

import pytest
from marshmallow import ValidationError

from sam.accounting.accounts import Account
from sam.manage.summaries import (
    _resolve_account,
    _resolve_facility_name,
    _resolve_machine,
    _resolve_machine_optional,
    _resolve_or_create_queue,
    _resolve_project,
    _resolve_resource,
    _resolve_user,
    upsert_archive_charge_summary,
    upsert_comp_charge_summary,
    upsert_disk_charge_summary,
)
from sam.resources.machines import Queue
from sam.schemas.charges import (
    ArchiveChargeSummaryInputSchema,
    CompChargeSummaryInputSchema,
    DiskChargeSummaryInputSchema,
)
from sam.summaries.archive_summaries import ArchiveChargeSummary
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary

from factories import (
    make_account,
    make_machine,
    make_project,
    make_queue,
    make_resource,
    make_resource_type,
    make_user,
    next_seq,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper builders for full charge-summary graphs
# ---------------------------------------------------------------------------


def _build_comp_graph(session):
    """Build a fresh User+Project+HPC Resource+Account+Machine+Queue chain.

    Returns a dict matching the legacy `comp_kwargs` shape. Uses an HPC
    resource type so the auto-machine-resolution path can fire without
    hitting snapshot-specific resources.
    """
    user = make_user(session)
    project = make_project(session, lead=user)
    rt = make_resource_type(session, resource_type=next_seq("HPCRT"))
    resource = make_resource(session, resource_type=rt)
    make_account(session, project=project, resource=resource)
    machine = make_machine(session, resource=resource)
    queue = make_queue(session, resource=resource)
    return {
        "user": user,
        "project": project,
        "resource": resource,
        "machine": machine,
        "queue": queue,
        "kwargs": dict(
            activity_date=date(2098, 6, 15),
            act_username=user.username,
            act_projcode=project.projcode,
            act_unix_uid=user.unix_uid,
            resource_name=resource.resource_name,
            queue_name=queue.queue_name,
            num_jobs=10,
            core_hours=1234.5,
            charges=987.65,
        ),
    }


def _build_storage_graph(session, *, resource_type_name: str):
    """Like `_build_comp_graph` but for storage (disk/archive) tests.

    `resource_type_name` is just a label for the test ID — the real DISK
    and ARCHIVE rows already exist in the snapshot and would collide on
    UNIQUE(resource_type), so we suffix it with a worker-namespaced
    sequence to keep this self-contained.
    """
    user = make_user(session)
    project = make_project(session, lead=user)
    rt = make_resource_type(
        session, resource_type=f"{resource_type_name}-{next_seq('rt')}"
    )
    resource = make_resource(session, resource_type=rt)
    make_account(session, project=project, resource=resource)
    return dict(
        activity_date=date(2098, 6, 15),
        act_username=user.username,
        act_projcode=project.projcode,
        act_unix_uid=user.unix_uid,
        resource_name=resource.resource_name,
        charges=25.0,
        number_of_files=500,
        bytes=1024 * 1024 * 1024,
        terabyte_years=0.5,
    )


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------


class TestResolverHelpers:

    def test_resolve_user_by_username(self, session):
        user = make_user(session)
        resolved = _resolve_user(session, user.username, user.unix_uid)
        assert resolved.user_id == user.user_id

    def test_resolve_user_by_uid_fallback(self, session):
        user = make_user(session)
        resolved = _resolve_user(session, "definitely_nonexistent_xyz", user.unix_uid)
        assert resolved.user_id == user.user_id

    def test_resolve_user_not_found(self, session):
        with pytest.raises(ValueError, match="User"):
            _resolve_user(session, "definitely_nonexistent_xyz", 999_999_999)

    def test_resolve_user_none_uid(self, session):
        with pytest.raises(ValueError, match="no uid"):
            _resolve_user(session, "definitely_nonexistent_xyz", None)

    def test_resolve_project_not_found(self, session):
        with pytest.raises(ValueError, match="ZZZZ9999"):
            _resolve_project(session, "ZZZZ9999")

    def test_resolve_resource_not_found(self, session):
        with pytest.raises(ValueError, match="FakeResource"):
            _resolve_resource(session, "FakeResource")

    def test_resolve_account_not_found(self, session):
        """Project exists but has no Account on the requested resource."""
        project = make_project(session)
        unattached_resource = make_resource(session)
        with pytest.raises(ValueError, match="No account"):
            _resolve_account(session, project, unattached_resource)

    def test_resolve_machine_not_found(self, session):
        resource = make_resource(session)
        with pytest.raises(ValueError, match="Machine"):
            _resolve_machine(session, "fake_machine_xyz", resource)

    def test_resolve_queue_not_found_no_create(self, session):
        resource = make_resource(session)
        with pytest.raises(ValueError, match="create_queue_if_missing"):
            _resolve_or_create_queue(session, "fake_queue_xyz", resource, False)

    def test_resolve_queue_creates_when_flag_set(self, session):
        resource = make_resource(session)
        new_name = next_seq("autoq")
        queue = _resolve_or_create_queue(session, new_name, resource, True)
        assert queue.queue_id is not None
        assert queue.queue_name == new_name
        assert queue.resource_id == resource.resource_id


# ---------------------------------------------------------------------------
# _resolve_facility_name
# ---------------------------------------------------------------------------


class TestResolveFacilityName:
    """Project.allocation_type → panel → facility chain.

    Building the full chain via factories would require Panel + AllocationType
    factories that we don't have yet. The legacy "happy path" test was
    already conditional ("if project.allocation_type and …, else None"), so
    we only port the negative case here.
    """

    def test_no_allocation_type(self, session):
        project = make_project(session)  # allocation_type_id is None by default
        assert project.allocation_type_id is None
        assert _resolve_facility_name(project) is None


# ---------------------------------------------------------------------------
# _resolve_machine_optional
# ---------------------------------------------------------------------------


class TestResolveMachineOptional:

    def test_explicit_name(self, session):
        resource = make_resource(session)
        machine = make_machine(session, resource=resource)
        result = _resolve_machine_optional(session, machine.name, resource)
        assert result.machine_id == machine.machine_id

    def test_explicit_name_not_found(self, session):
        resource = make_resource(session)
        with pytest.raises(ValueError, match="Machine"):
            _resolve_machine_optional(session, "fake_machine_xyz", resource)

    def test_auto_resolve_single_machine(self, session):
        resource = make_resource(session)
        machine = make_machine(session, resource=resource)
        result = _resolve_machine_optional(session, None, resource)
        assert result.machine_id == machine.machine_id

    def test_error_on_multiple_machines(self, session):
        resource = make_resource(session)
        m1 = make_machine(session, resource=resource)
        m2 = make_machine(session, resource=resource)
        with pytest.raises(ValueError, match="machine_name must be provided") as exc_info:
            _resolve_machine_optional(session, None, resource)
        assert m1.name in str(exc_info.value)
        assert m2.name in str(exc_info.value)

    def test_error_on_zero_machines(self, session):
        resource = make_resource(session)  # no machines
        with pytest.raises(ValueError, match="no machines"):
            _resolve_machine_optional(session, None, resource)


# ---------------------------------------------------------------------------
# upsert_comp_charge_summary
# ---------------------------------------------------------------------------


class TestUpsertCompChargeSummary:

    def test_insert_new(self, session):
        graph = _build_comp_graph(session)
        record, action = upsert_comp_charge_summary(session, **graph["kwargs"])
        assert action == "created"
        assert record.charge_summary_id is not None
        assert record.activity_date == graph["kwargs"]["activity_date"]
        assert record.charges == graph["kwargs"]["charges"]

    def test_insert_with_explicit_machine(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], machine_name=graph["machine"].name)
        record, action = upsert_comp_charge_summary(session, **kwargs)
        assert action == "created"
        assert record.machine == graph["machine"].name

    def test_insert_auto_resolves_machine(self, session):
        graph = _build_comp_graph(session)
        assert "machine_name" not in graph["kwargs"]  # fixture omits it
        record, action = upsert_comp_charge_summary(session, **graph["kwargs"])
        assert action == "created"
        assert record.machine == graph["machine"].name
        assert record.machine_id == graph["machine"].machine_id

    def test_update_existing(self, session):
        graph = _build_comp_graph(session)
        record1, action1 = upsert_comp_charge_summary(session, **graph["kwargs"])
        assert action1 == "created"

        updated_kwargs = dict(graph["kwargs"], charges=555.55, num_jobs=20)
        record2, action2 = upsert_comp_charge_summary(session, **updated_kwargs)
        assert action2 == "updated"
        assert record2.charge_summary_id == record1.charge_summary_id
        assert record2.charges == 555.55
        assert record2.num_jobs == 20

    def test_act_fields_immutable_on_update(self, session):
        graph = _build_comp_graph(session)
        record, _ = upsert_comp_charge_summary(session, **graph["kwargs"])
        orig = (record.act_username, record.act_projcode, record.act_unix_uid)

        updated_kwargs = dict(graph["kwargs"], charges=111.11)
        record2, action = upsert_comp_charge_summary(session, **updated_kwargs)
        assert action == "updated"
        assert (record2.act_username, record2.act_projcode, record2.act_unix_uid) == orig

    def test_resolved_fields_default_from_act(self, session):
        graph = _build_comp_graph(session)
        record, _ = upsert_comp_charge_summary(session, **graph["kwargs"])
        assert record.username == graph["kwargs"]["act_username"]
        assert record.projcode == graph["kwargs"]["act_projcode"]
        assert record.unix_uid == graph["kwargs"]["act_unix_uid"]

    def test_zero_unix_uid_preserved(self, session):
        """unix_uid=0 stored as 0, not coerced to act_unix_uid."""
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], unix_uid=0)
        record, _ = upsert_comp_charge_summary(session, **kwargs)
        assert record.unix_uid == 0

    def test_put_semantics_nulls_optional_fields(self, session):
        """Second call omitting `cos` overwrites the stored value with NULL."""
        graph = _build_comp_graph(session)
        kwargs_with_cos = dict(graph["kwargs"], cos=5)
        record, _ = upsert_comp_charge_summary(session, **kwargs_with_cos)
        assert record.cos == 5

        record2, action = upsert_comp_charge_summary(session, **graph["kwargs"])
        assert action == "updated"
        assert record2.cos is None

    def test_facility_name_override(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], facility_name="TEST_FACILITY")
        record, _ = upsert_comp_charge_summary(session, **kwargs)
        assert record.facility_name == "TEST_FACILITY"

    def test_invalid_user_raises(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], act_username="nobody_xyz", act_unix_uid=999_999_999)
        with pytest.raises(ValueError, match="User"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_project_raises(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], act_projcode="ZZZZ9999")
        with pytest.raises(ValueError, match="Project"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_resource_raises(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], resource_name="FakeResource")
        with pytest.raises(ValueError, match="Resource"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_invalid_machine_raises(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], machine_name="fake_machine_xyz")
        with pytest.raises(ValueError, match="Machine"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_missing_queue_no_flag_raises(self, session):
        graph = _build_comp_graph(session)
        kwargs = dict(graph["kwargs"], queue_name="fake_queue_xyz")
        with pytest.raises(ValueError, match="create_queue_if_missing"):
            upsert_comp_charge_summary(session, **kwargs)

    def test_create_queue_if_missing(self, session):
        graph = _build_comp_graph(session)
        new_queue_name = next_seq("autoq")
        kwargs = dict(
            graph["kwargs"],
            queue_name=new_queue_name,
            create_queue_if_missing=True,
        )
        record, action = upsert_comp_charge_summary(session, **kwargs)
        assert action == "created"
        assert record.queue == new_queue_name
        assert record.queue_id is not None


# ---------------------------------------------------------------------------
# upsert_disk/archive_charge_summary (parametrized)
# ---------------------------------------------------------------------------


STORAGE_CASES = [
    pytest.param(DiskChargeSummary, upsert_disk_charge_summary, "DISK", id="disk"),
    pytest.param(ArchiveChargeSummary, upsert_archive_charge_summary, "ARCHIVE", id="archive"),
]


class TestUpsertStorageChargeSummaries:

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_insert_new(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        record, action = upsert_fn(session, **kwargs)
        assert action == "created"
        assert record.charges == 25.0

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_update_existing(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        record1, action1 = upsert_fn(session, **kwargs)
        assert action1 == "created"

        updated = dict(kwargs, charges=50.0)
        record2, action2 = upsert_fn(session, **updated)
        assert action2 == "updated"
        assert record2.charges == 50.0

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_act_fields_immutable(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        record, _ = upsert_fn(session, **kwargs)
        orig = (record.act_username, record.act_projcode, record.act_unix_uid)

        updated = dict(kwargs, charges=99.9)
        record2, _ = upsert_fn(session, **updated)
        assert (record2.act_username, record2.act_projcode, record2.act_unix_uid) == orig

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_invalid_user(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        kwargs["act_username"] = "nobody_xyz"
        kwargs["act_unix_uid"] = 999_999_999
        with pytest.raises(ValueError, match="User"):
            upsert_fn(session, **kwargs)

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_invalid_project(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        kwargs["act_projcode"] = "ZZZZ9999"
        with pytest.raises(ValueError, match="Project"):
            upsert_fn(session, **kwargs)

    @pytest.mark.parametrize("model_cls, upsert_fn, res_type", STORAGE_CASES)
    def test_invalid_resource(self, session, model_cls, upsert_fn, res_type):
        kwargs = _build_storage_graph(session, resource_type_name=res_type)
        kwargs["resource_name"] = "FakeResource"
        with pytest.raises(ValueError, match="Resource"):
            upsert_fn(session, **kwargs)


# ---------------------------------------------------------------------------
# Marshmallow input schemas (zero DB)
# ---------------------------------------------------------------------------


class TestInputSchemas:

    def test_comp_valid_input(self):
        data = CompChargeSummaryInputSchema().load({
            "activity_date": "2025-01-15",
            "act_username": "benkirk",
            "act_projcode": "SCSG0001",
            "act_unix_uid": 12345,
            "resource_name": "Derecho",
            "machine_name": "derecho",
            "queue_name": "main",
            "num_jobs": 10,
            "core_hours": 1234.5,
            "charges": 987.65,
        })
        assert data["activity_date"] == date(2025, 1, 15)
        assert data["act_username"] == "benkirk"
        assert data["num_jobs"] == 10

    def test_comp_missing_required(self):
        with pytest.raises(ValidationError) as exc_info:
            CompChargeSummaryInputSchema().load({
                "act_username": "benkirk",
                "act_projcode": "SCSG0001",
                "resource_name": "Derecho",
                "machine_name": "derecho",
                "queue_name": "main",
                "num_jobs": 10,
                "core_hours": 100.0,
                "charges": 50.0,
            })
        assert "activity_date" in exc_info.value.messages

    def test_comp_invalid_date(self):
        with pytest.raises(ValidationError):
            CompChargeSummaryInputSchema().load({
                "activity_date": "not-a-date",
                "act_username": "benkirk",
                "act_projcode": "SCSG0001",
                "resource_name": "Derecho",
                "machine_name": "derecho",
                "queue_name": "main",
                "num_jobs": 10,
                "core_hours": 100.0,
                "charges": 50.0,
            })

    def test_comp_negative_num_jobs(self):
        with pytest.raises(ValidationError):
            CompChargeSummaryInputSchema().load({
                "activity_date": "2025-01-15",
                "act_username": "benkirk",
                "act_projcode": "SCSG0001",
                "resource_name": "Derecho",
                "machine_name": "derecho",
                "queue_name": "main",
                "num_jobs": -1,
                "core_hours": 100.0,
                "charges": 50.0,
            })

    def test_comp_machine_name_optional(self):
        data = CompChargeSummaryInputSchema().load({
            "activity_date": "2025-01-15",
            "act_username": "benkirk",
            "act_projcode": "SCSG0001",
            "resource_name": "Derecho",
            "queue_name": "main",
            "num_jobs": 10,
            "core_hours": 100.0,
            "charges": 50.0,
        })
        assert data["machine_name"] is None

    def test_comp_queue_flag_defaults_false(self):
        data = CompChargeSummaryInputSchema().load({
            "activity_date": "2025-01-15",
            "act_username": "benkirk",
            "act_projcode": "SCSG0001",
            "resource_name": "Derecho",
            "machine_name": "derecho",
            "queue_name": "main",
            "num_jobs": 10,
            "core_hours": 100.0,
            "charges": 50.0,
        })
        assert data["create_queue_if_missing"] is False

    def test_disk_valid_input(self):
        data = DiskChargeSummaryInputSchema().load({
            "activity_date": "2025-01-15",
            "act_username": "benkirk",
            "act_projcode": "SCSG0001",
            "resource_name": "Campaign Store",
            "charges": 25.0,
            "number_of_files": 500,
            "bytes": 1073741824,
            "terabyte_years": 0.5,
        })
        assert data["charges"] == 25.0
        assert data["number_of_files"] == 500

    def test_archive_valid_input(self):
        data = ArchiveChargeSummaryInputSchema().load({
            "activity_date": "2025-01-15",
            "act_username": "benkirk",
            "act_projcode": "SCSG0001",
            "resource_name": "HPSS",
            "charges": 60.0,
            "number_of_files": 1000,
            "terabyte_years": 1.2,
        })
        assert data["charges"] == 60.0

    def test_storage_negative_bytes(self):
        with pytest.raises(ValidationError):
            DiskChargeSummaryInputSchema().load({
                "activity_date": "2025-01-15",
                "act_username": "benkirk",
                "act_projcode": "SCSG0001",
                "resource_name": "Campaign Store",
                "charges": 25.0,
                "bytes": -1,
            })

    def test_storage_missing_required(self):
        with pytest.raises(ValidationError) as exc_info:
            ArchiveChargeSummaryInputSchema().load({
                "activity_date": "2025-01-15",
                "act_username": "benkirk",
                "act_projcode": "SCSG0001",
                "resource_name": "HPSS",
            })
        assert "charges" in exc_info.value.messages
