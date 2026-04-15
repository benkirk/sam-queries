"""Plain builder functions for synthetic test data — Layer 2 of the
two-layer test data strategy (see new_tests/conftest.py).

Each builder takes `session` as its first positional arg, auto-builds the
minimum FK graph it needs, calls `session.flush()` (never `commit()`), and
returns the flushed instance with its primary key populated.

Tests that need "any row from the snapshot" should use the `any_*` Layer 1
fixtures from conftest.py instead — never blend the two strategies.
"""
from ._seq import next_int, next_seq, reset_seq
from .core import make_organization, make_user
from .operational import make_wallclock_exemption
from .projects import (
    make_account,
    make_allocation,
    make_aoi,
    make_aoi_group,
    make_facility,
    make_project,
)
from .resources import make_machine, make_queue, make_resource, make_resource_type

__all__ = [
    "next_int",
    "next_seq",
    "reset_seq",
    "make_organization",
    "make_user",
    "make_resource_type",
    "make_resource",
    "make_machine",
    "make_queue",
    "make_wallclock_exemption",
    "make_facility",
    "make_aoi_group",
    "make_aoi",
    "make_project",
    "make_account",
    "make_allocation",
]
