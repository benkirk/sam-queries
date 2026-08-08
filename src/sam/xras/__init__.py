"""XRAS action handling — the write side of the XRAS integration.

The read side (six GET endpoints) lives in ``sam.queries.xras_access``; the audit
tables live in ``sam.integration.xras``; the wire schema lives in
``sam.schemas.forms.xras``. This package is what happens *between* parsing a
``POST /api/xras/v1/actions`` body and writing to the database.

Nothing here imports Flask. Handlers take a ``Session`` and return a result, so
they are exercised by unit tests directly and reached from two callers that are
not the same: the live route (``webapp/api/xras/actions.py``) and replay
(``webapp/api/xras/replay.py``).

This module re-exports the **error vocabulary only**, so that importing ``sam.xras``
costs nothing but ``errors.py``. The submodules that touch the ORM — ``extractors``,
and the handlers to come — are imported by name (``from sam.xras.extractors import
resolve_contract``), which also keeps the dependency direction visible at each call
site.

The design and the measured production data behind it are in
``docs/plans/XRAS_SPRINT_C.md``; the wire contract is in
``docs/plans/XRAS_REIMPLEMENTATION.md``.
"""

from .errors import (
    ActionErrors,
    XrasActionRejected,
    # Error-string builders, one per message legacy can emit. See the module
    # docstring for why these are functions rather than format strings.
    missing_title,
    missing_pi_role,
    pi_not_in_database,
    pi_not_active,
    manager_not_in_database,
    manager_not_active,
    username_missing,
    username_inactive,
    no_resource_for_key,
    no_resource_for_name,
    awarded_amount_missing,
    could_not_convert_amount,
    missing_date,
    could_not_convert_date,
    extension_end_date_before_existing,
    update_end_date_before_existing,
    all_end_dates_null_or_past,
    cannot_find_contract,
    ambiguous_contract,
    mnemonic_external_failed,
    mnemonic_internal_failed,
    no_affiliation_for_pi,
    no_fos_objects,
    aoi_not_in_database,
    allocation_type_undetermined,
    no_allocation_type_for_pair,
    transfer_one_source_only,
    transfer_requires_source,
    transfer_requires_destination,
    transfer_source_has_no_allocation,
    transfer_credit_exceeds_debit,
)

__all__ = [
    'ActionErrors',
    'XrasActionRejected',
    'missing_title',
    'missing_pi_role',
    'pi_not_in_database',
    'pi_not_active',
    'manager_not_in_database',
    'manager_not_active',
    'username_missing',
    'username_inactive',
    'no_resource_for_key',
    'no_resource_for_name',
    'awarded_amount_missing',
    'could_not_convert_amount',
    'missing_date',
    'could_not_convert_date',
    'extension_end_date_before_existing',
    'update_end_date_before_existing',
    'all_end_dates_null_or_past',
    'cannot_find_contract',
    'ambiguous_contract',
    'mnemonic_external_failed',
    'mnemonic_internal_failed',
    'no_affiliation_for_pi',
    'no_fos_objects',
    'aoi_not_in_database',
    'allocation_type_undetermined',
    'no_allocation_type_for_pair',
    'transfer_one_source_only',
    'transfer_requires_source',
    'transfer_requires_destination',
    'transfer_source_has_no_allocation',
    'transfer_credit_exceeds_debit',
]
