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
from typing import List, Optional

from sam.accounting.allocations import Allocation
from sam.base import normalize_end_date
from sam.manage.extend import extend_account_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..errors import ActionErrors

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


def _get(obj, key: str):
    """Read one wire field from a loaded dict or an attribute-carrying object."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def parse_action_end_date(action, errs: ActionErrors) -> Optional[datetime]:
    """``ProjectAllocationActionCommandsFactoryBase.getEndDate()``.

    Blank → ``Missing end date for allocation(s)``; unparseable → ``Could not convert
    end date for allocation(s)``. Two separate strings — § 3.4 of the reference doc
    collapses them into one slashed line, which is one of its seven errors.

    A valid date is returned at **end of day**, matching legacy's
    ``getDateAtEndOfDay`` and SAM's own 23:59:59 end-date convention. The two agree,
    which is why :func:`sam.base.normalize_end_date` can do the work.
    """
    raw = _get(action, 'actionEndDate')
    if raw is None or not str(raw).strip():
        errs.report(e.missing_date('end'))
        return None
    try:
        parsed = datetime.strptime(str(raw).strip(), '%Y-%m-%d')
    except ValueError:
        errs.report(e.could_not_convert_date('end'))
        return None
    return normalize_end_date(parsed)


def effective_end_date(allocation: Allocation) -> Optional[datetime]:
    """``Allocation.getEndDate()`` — the stored end, clamped by decommission.

    Legacy's getter is not a plain column read: when the account's resource has a
    ``decommission_date``, it returns ``min(stored end, decommission)``, and a **null**
    stored end reads through as the decommission date itself. Both the shrink test and
    the latest-allocation search go through it, so a port that read ``end_date``
    directly would compare different quantities than legacy did.

    The account filter already excludes resources decommissioned *before now*, so the
    clamp only bites on an announced future decommission — which is exactly when it
    matters, because that is when an allocation is quietly shorter than it looks.
    """
    resource = allocation.account.resource if allocation.account else None
    decommission = getattr(resource, 'decommission_date', None)
    if decommission is None:
        return allocation.end_date
    if allocation.end_date is None:
        return decommission
    return min(allocation.end_date, decommission)


def account_is_active(account, now: datetime) -> bool:
    """``Account.isActive(date)`` — ``project.isActive() && resource.isCommissioned(date)
    && !creationTime.after(date)``, minus the third conjunct. See below.

    ⚠️ **Not ``Account.is_active``.** SAM's hybrid on this model comes from
    ``SoftDeleteMixin`` and means "not deleted", which is a different question
    entirely — using it here would extend allocations on decommissioned resources and
    on inactive projects. The house rule (CLAUDE.md § 5) is to prefer the hybrid, and
    this is the case where doing so would be wrong; the composite is built from the
    other models' documented predicates rather than from raw columns.

    ⚠️ **``!creationTime.after(now)`` is deliberately not ported.** It compares two
    clocks that are not the same clock: ``account.creation_time`` carries
    ``server_default=CURRENT_TIMESTAMP`` and resolves in the **MySQL server's**
    timezone, which is UTC in the dev and CI containers, while ``now`` is
    ``datetime.now()`` in SAM's naive-Mountain convention. Measured, today, against the
    test container: ``NOW()`` returns 12:45 while Python returns 06:45 — a six-hour
    skew, in the direction that makes every account created in the last six hours look
    like it was created in the future.

    The conjunct can only ever *exclude*, so honouring it under skew means an Extension
    posted shortly after a New silently skips the account it should extend, reports
    ``processed``, and writes nothing. Dropping it is a no-op in any deployment where
    the two clocks agree — which is the intent — and removes the failure mode where
    they do not. This repo has already been bitten once by the same UTC default (see
    ``webapp/api/xras/actions.py``'s ``received_time`` comment).

    The soft-delete check is kept **as well**, as a declared divergence: legacy has no
    equivalent, but extending a deleted account would be wrong regardless. Unobservable
    today — production has zero deleted accounts out of 17,989.
    """
    if not account.is_active:                      # SoftDeleteMixin: not deleted
        return False
    if account.project is None or not account.project.is_active:
        return False
    if account.resource is None or not account.resource.is_commissioned_at(now):
        return False
    return True


def latest_allocation(account) -> Optional[Allocation]:
    """``Account.getLatestAllocation()`` — max end date, with one short-circuit.

    ⚠️ An allocation whose **effective** end date is null is returned *immediately*,
    regardless of position or of what else the account holds. Legacy returns from
    inside the loop, so this is iteration-order dependent when an account has two
    open-ended allocations — a shape that should not exist and, if it did, would make
    legacy's own answer arbitrary. Reproduced rather than tidied, and flagged here
    because "latest" is a misleading name for it.
    """
    latest = None
    latest_end = None
    for allocation in account.allocations:
        end = effective_end_date(allocation)
        if end is None:
            return allocation
        if latest is None or latest_end < end:
            latest, latest_end = allocation, end
    return latest


def handle_extension(session, action) -> DispatchResult:
    """Extend the latest allocation of every active account on the project.

    Raises:
        XrasActionRejected: the new end date is missing, unparseable, or would
            shrink at least one account's allocation. Nothing is written.
    """
    projcode = (_get(action, 'requestNumber') or '').strip()
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
