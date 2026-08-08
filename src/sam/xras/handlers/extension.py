"""Extension — 60% of production traffic, 98.5% of it successful, and the smallest
surface of the six.

Its only input is ``actionEndDate``. That is not a simplification: the
``ExtendProjectAssembler`` composes **one** factory, the extend-allocation one, so
the Extension path never constructs the project factory or the roster factory at all.
It therefore does **not** validate the title, the PI, the Allocation Manager, or any
roster member — none of ``Missing title``, ``Missing pi role`` or ``Username %s is
missing`` can be emitted by an Extension. Worth stating because the corpus makes it
look otherwise: both Extensions carry a populated ``roles[]`` array that nothing reads.

⚠️ **``resources[]`` is ignored entirely.** Legacy walks the project's *accounts*, not
the requested resources, taking one allocation from each. Both corpus Extensions send
``resources: []`` and both extended real allocations, which is only possible because
the array is never consulted. A handler that iterated ``resources[]`` would be a no-op
on 100% of observed Extension traffic — it would return success having written nothing,
and the audit row would say ``processed``.

The shape
---------
Assemble → check once → execute. Every account is examined and every problem reported
before anything is written; one un-extendable account aborts the whole action at
``raise_if_any()``. That is legacy's behaviour and it is the reason the 422 is worth
reading: an operator sees all of it at once.

Verified against ``~/codes/sam`` at tag 2.0.3
(``ExtendProjectAllocationActionCommandsFactory``, ``DefaultExtendAllocationCommand``,
``Allocation.extend``). See ``docs/plans/XRAS_SPRINT_C.md`` § *Extension*.
"""

import logging
from datetime import datetime
from typing import List

from sam.accounting.allocations import Allocation
from sam.manage.extend import extend_account_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from ..wire import get_field
from ._allocations import (           # noqa: F401  — re-exported, see the shim note
    account_is_active,
    effective_end_date,
    latest_allocation,
)
from ._fields import parse_action_end_date  # noqa: F401  — re-exported

logger = logging.getLogger(__name__)

__all__ = ['handle_extension', 'EXTENSION_COMMENT']

#: ``transaction_comment`` on every row this handler writes.
#:
#: ⚠️ **A Java class name, reproduced on purpose.** Legacy builds it as
#: ``action.getClass().getSimpleName() + " Extension Request"``, so the string in the
#: database is an implementation detail that leaked years ago and then became the
#: thing operators grep for. Production holds **1,553** rows of this exact spelling and
#: **8,552** of the pre-2025-10 ``XRAS Extension Request``. Hardcoded rather than
#: derived, because deriving it from a Python class name would silently produce a third
#: spelling the moment anyone renamed a class.
EXTENSION_COMMENT = 'XrasAction Extension Request'


def handle_extension(session, action) -> DispatchResult:
    """Extend the latest allocation of every active account on the project.

    Raises:
        XrasActionRejected: the new end date is missing, unparseable, or would
            shrink at least one account's allocation. Nothing is written.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode)
    errs = ActionErrors()

    new_end = parse_action_end_date(action, errs)

    # Assemble: examine every account, report everything, write nothing.
    targets: List[Allocation] = []
    now = datetime.now()
    if project is not None and new_end is not None:
        for account in project.accounts:
            if not account_is_active(account, now):
                continue
            if not account.allocations:              # Account.hasAllocations()
                continue
            allocation = latest_allocation(account)
            if allocation is None:                   # pragma: no cover - defensive
                continue

            existing_end = effective_end_date(allocation)
            if existing_end is not None and new_end < existing_end:
                # Legacy drops *this* allocation and carries on, so a second bad
                # account reports too — then the accumulated error aborts everything.
                errs.report(e.extension_end_date_before_existing(
                    existing_end.strftime('%Y-%m-%d')))
                continue
            targets.append(allocation)

    # Check once. Nothing above opened a transaction.
    errs.raise_if_any()

    # Execute.
    extended: List[Allocation] = []
    with management_transaction(session):
        for allocation in targets:
            extended.extend(extend_account_allocation(
                session, allocation,
                new_end=new_end,
                comment=EXTENSION_COMMENT,
            ))

    if not extended:
        # Every target was already at the requested end date. Legacy reports success
        # here too — its `doExtend` returns early per node and the action still
        # completes — and this is a candidate explanation for the "2 successful posts
        # that mutated nothing" in § 1.2 of the reference doc.
        logger.info(
            'XRAS extension for %s changed nothing: %d account(s) already end %s',
            projcode, len(targets), new_end.strftime('%Y-%m-%d'))

    return DispatchResult(status='processed', service='extend', projcode=projcode)


register('extend', handle_extension)
