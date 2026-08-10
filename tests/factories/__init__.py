"""Plain builder functions for synthetic test data — Layer 2 of the
two-layer test data strategy (see new_tests/conftest.py).

Each builder takes `session` as its first positional arg, auto-builds the
minimum FK graph it needs, calls `session.flush()` (never `commit()`), and
returns the flushed instance with its primary key populated.

Tests that need "any row from the snapshot" should use the `any_*` Layer 1
fixtures from conftest.py instead — never blend the two strategies.
"""
from ._seq import next_date, next_int, next_seq, reset_seq
from .core import (
    make_adhoc_group,
    make_gid_allocation,
    make_institution,
    make_mnemonic_code,
    make_organization,
    make_user,
    make_user_institution,
    make_user_organization,
)
from .notify import make_notification_log
from .operational import make_wallclock_exemption
from .xras import (
    make_xras_action,
    make_xras_activation_event,
    make_xras_key_mapping,
)
from .projects import (
    make_account,
    make_allocation,
    make_allocation_transaction,
    make_aoi,
    make_aoi_group,
    make_charge_adjustment,
    make_contract,
    make_contract_source,
    make_nsf_program,
    make_facility,
    make_project,
    make_project_contract,
    make_project_organization,
)
from .resources import (
    make_disk_resource_root_directory,
    make_machine,
    make_queue,
    make_resource,
    make_resource_type,
)
from .security import make_api_credentials, make_role
from .summaries import make_comp_charge_summary

__all__ = [
    "next_date",
    "next_int",
    "next_seq",
    "reset_seq",
    "make_adhoc_group",
    "make_notification_log",
    "make_gid_allocation",
    "make_institution",
    "make_mnemonic_code",
    "make_organization",
    "make_user",
    "make_user_institution",
    "make_user_organization",
    "make_resource_type",
    "make_resource",
    "make_disk_resource_root_directory",
    "make_machine",
    "make_queue",
    "make_comp_charge_summary",
    "make_wallclock_exemption",
    "make_xras_action",
    "make_xras_activation_event",
    "make_xras_key_mapping",
    "make_facility",
    "make_aoi_group",
    "make_aoi",
    "make_project",
    "make_contract_source",
    "make_contract",
    "make_nsf_program",
    "make_project_contract",
    "make_project_organization",
    "make_account",
    "make_allocation",
    "make_allocation_transaction",
    "make_charge_adjustment",
    "make_role",
    "make_api_credentials",
]
