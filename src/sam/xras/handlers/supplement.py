"""Supplement — 15% of traffic, 100% successful, and Extension's mirror image.

Where Extension ignores ``resources[]`` and walks accounts, this walks
``resources[]`` and ignores everything else. Per requested resource:

.. code-block:: java

    if (allocation == null)                       return buildAddAllocationCommand(resource);
    else if (getTransactionAmount(resource) > 0)  return buildSupplementAllocationCommand(...);
    return null;                                   // <= 0 silently dropped

⚠️ **``awardedAmount`` is the INCREMENT, not the new total.** ``SUPPLEMENT`` replays
as ``addAmount(transaction_amount)``. This is the single most consequential porting
semantic in the sprint: reading it as an absolute would overwrite a multi-million-hour
allocation with a quarter-million-hour supplement, silently, and the resulting number
would look entirely plausible.

The lookup is ``Project.getAccount(name)`` — a plain scan over **all** accounts,
active or not, matching on resource name case-insensitively. Note the asymmetry with
Extension, which filters accounts hard. A supplement therefore lands on an account
whose resource is decommissioned, where an extension would skip it.

Like Extension, this assembler composes only its own factory: no title, PI or roster
validation. Unlike Extension, it can also *create* an allocation, and the create branch
uses **today** plus a derived end date rather than the action's own dates.

Verified against ``~/codes/sam`` at tag 2.0.3
(``SupplementProjectAllocationActionCommandsFactory``, ``Allocation.supplement``).
See ``docs/plans/XRAS_SPRINT_C.md`` § *Supplement*.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sam.accounting.allocations import Allocation
from sam.base import normalize_end_date
from sam.integration.xras import XrasResourceRepositoryKeyResource
from sam.manage.allocations import create_allocation, supplement_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project
from sam.resources.resources import Resource

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from ..extractors import select_allocation_type_parms
from ..roster import normalize_username
from ..wire import get_field
from .extension import effective_end_date, latest_allocation

logger = logging.getLogger(__name__)

__all__ = [
    'handle_supplement',
    'resolve_resource',
    'transaction_amount',
    'resource_comment',
    'auth_at_panel_meeting',
    'account_for_resource',
    'new_allocation_end_date',
]

#: ``AllocationTypeIdExtractor``'s two panel-authorised types. ``getAuthAtPanelMeeting()``
#: is ``true`` iff the resolved type is one of these.
_PANEL_AUTHORISED = frozenset({'CSL', 'CHAP'})


def resolve_resource(session, wire_resource, errs: ActionErrors) -> Optional[Resource]:
    """``resources[].key`` → a SAM resource, via ``xras_resource_repository_key_resource``.

    Reports ``No resource found in SAM corresponding to key %s`` — the *key* variant.
    The roster path has its own string naming the resource **name** instead; both can
    fire for one action, which is why they are separate builders.

    ⚠️ Only **13** mapping rows exist and 11 active SAM resources have none, so this is
    a live failure mode rather than a defensive branch. An award citing Derecho's GPU
    partition or Gust fails here, and the fix is a data fix.

    ⚠️ Legacy calls ``getResourceName`` **twice** per resource on some paths, so an
    unmapped key reports twice and collapses to one line in the accumulator. That is
    the dedup working as designed, and it is why the container is a set.
    """
    key = get_field(wire_resource, 'key')
    row = None
    if key is not None:
        row = (session.query(XrasResourceRepositoryKeyResource)
               .filter(XrasResourceRepositoryKeyResource.resource_repository_key == key)
               .first())
    if row is None or row.resource is None:
        errs.report(e.no_resource_for_key('' if key is None else str(key)))
        return None
    return row.resource


def transaction_amount(wire_resource, errs: ActionErrors) -> Optional[float]:
    """``getTransactionAmount`` — blank reports, unparseable reports, else a float.

    ⚠️ **A declared divergence lives here.** Legacy's caller then does
    ``getTransactionAmount(resource) > 0``, which **unboxes a null ``Float``** when the
    amount was blank or unparseable — throwing a ``NullPointerException`` *inside*
    assembly, so ``throwExceptionIfErrors`` never runs and the operator receives a bare
    stack-trace class name instead of ``Awarded amount missing``. Returning ``None`` and
    letting the caller check keeps the diagnostic, which is the entire point of the 422.

    ``Could not convert awarded amount "%s"␣␣to float`` has **two spaces** before
    ``to float``. Reproduced; see :mod:`sam.xras.errors`.
    """
    raw = get_field(wire_resource, 'awardedAmount')
    if raw is None or not str(raw).strip():
        errs.report(e.awarded_amount_missing())
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        errs.report(e.could_not_convert_amount(str(raw)))
        return None


def resource_comment(wire_resource) -> Optional[str]:
    """``getComment`` — the normalized ``resources[].comments``, or ``None`` if blank.

    Same ``StringUtil.normalize`` the roster uses on usernames, so an accented comment
    is ASCII-folded before it reaches ``transaction_comment``.
    """
    comment = normalize_username(get_field(wire_resource, 'comments')).strip()
    return comment or None


def auth_at_panel_meeting(session, action) -> bool:
    """``getAuthAtPanelMeeting()`` — true iff the allocation type is CSL or CHAP.

    ⚠️ The branch is **inverted** from what you would expect
    (``ProjectAllocationActionCommandsFactoryBase:96-114``): when the payload *carries*
    an ``allocationType`` it runs the eleven-strategy chain; when it does **not**, it
    reads the *existing project's* stored type and looks that up by name. Both arms are
    reproduced. Set on 1,264 of the 3,203 integration-written SUPPLEMENT rows in
    production, so it is not vestigial.
    """
    if get_field(action, 'allocationType'):
        parms = select_allocation_type_parms(action)
        return parms is not None and parms.allocation_type in _PANEL_AUTHORISED

    projcode = (get_field(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode) if projcode else None
    stored = getattr(project.allocation_type, 'allocation_type', None) if project else None
    return stored in _PANEL_AUTHORISED


def account_for_resource(project: Project, resource: Resource):
    """``Project.getAccount(resourceName)`` — a scan over **all** accounts.

    ⚠️ Deliberately unfiltered. Extension's ``account.isActive()`` gate does not apply
    here, so a supplement lands on an account whose project is inactive or whose
    resource is decommissioned. Matching on the resource *name*, case-insensitively
    (``Account.isForResource`` uses ``equalsIgnoreCase``), rather than on the id —
    because that is the join legacy makes, and the two can disagree if two resource
    rows ever share a name.
    """
    wanted = (resource.resource_name or '').casefold()
    for account in project.accounts:
        if account.resource is not None and \
                (account.resource.resource_name or '').casefold() == wanted:
            return account
    return None


def new_allocation_end_date(project: Project, start_date: datetime) -> Optional[datetime]:
    """``findLatestProjectEndDateForNewAllocation`` — contract first, then allocation.

    Latest **contract** end date if it is not before *start_date*; else the latest
    **allocation** end date under the same test; else ``None``, which the caller turns
    into ``All contract and allocation end dates are null or past for project [%s]``.

    ⚠️ Kept bug-for-bug: the create branch derives its window from *today* and the
    project's own history, and **never looks at the action's ``actionBeginDate`` or
    ``actionEndDate``**. A Supplement that creates an allocation therefore gets dates
    XRAS did not ask for. Reproduced because the alternative is inventing a policy, and
    because 100% of Supplement traffic succeeds under the current rule.
    """
    contract_ends = [pc.contract.end_date for pc in project.contracts
                     if pc.contract is not None and pc.contract.end_date is not None]
    latest = max(contract_ends, default=None)
    if latest is not None and latest >= start_date:
        return latest

    allocation_ends = []
    for account in project.accounts:
        if not account.allocations:
            continue
        allocation = latest_allocation(account)
        end = effective_end_date(allocation) if allocation is not None else None
        if end is not None:
            allocation_ends.append(end)
    latest = max(allocation_ends, default=None)
    if latest is not None and latest >= start_date:
        return latest
    return None


def _plan(session, action, errs: ActionErrors) -> Tuple[List[tuple], List[tuple]]:
    """Assemble, reporting everything. Returns ``(supplements, creations)``.

    Pure: examines the whole ``resources[]`` array and writes nothing, so one bad
    resource still lets the rest report their own problems before the single
    ``raise_if_any()``.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode)
    if project is None:                              # pragma: no cover - dispatcher checked
        return [], []

    auth = auth_at_panel_meeting(session, action)
    comment_for = resource_comment
    supplements: List[tuple] = []
    creations: List[tuple] = []

    for wire_resource in get_field(action, 'resources') or ():
        resource = resolve_resource(session, wire_resource, errs)
        if resource is None:
            continue

        account = account_for_resource(project, resource)
        allocation = latest_allocation(account) if account is not None else None
        amount = transaction_amount(wire_resource, errs)

        if allocation is None:
            # Create branch. Note the amount is still required — legacy passes it
            # straight into the add command, where a null would fail validation.
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end = new_allocation_end_date(project, start)
            if end is None:
                errs.report(e.all_end_dates_null_or_past(projcode))
                continue
            if amount is None:
                continue
            creations.append((resource, amount, comment_for(wire_resource),
                              start, normalize_end_date(end), auth))
            continue

        if amount is None:
            continue
        if amount <= 0:
            # Legacy drops these silently — `return null` with no report. Logged
            # rather than reported, so the action still succeeds as it does today,
            # but the drop is visible to whoever is triaging.
            logger.warning(
                'XRAS supplement for %s on %s has a non-positive amount (%s); '
                'legacy drops it silently and so do we',
                projcode, resource.resource_name, amount)
            continue

        supplements.append((allocation, amount, comment_for(wire_resource), auth))

    return supplements, creations


def handle_supplement(session, action) -> DispatchResult:
    """Add to each requested resource's allocation, creating it where there is none.

    Raises:
        XrasActionRejected: an unmapped resource key, a missing or unparseable amount,
            or a create branch with no usable end date. Nothing is written.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    errs = ActionErrors()
    supplements, creations = _plan(session, action, errs)

    errs.raise_if_any()

    project = Project.get_by_projcode(session, projcode)
    with management_transaction(session):
        for allocation, amount, comment, auth in supplements:
            supplement_allocation(session, allocation, amount=amount,
                                  comment=comment, auth_at_panel_mtg=auth)
        for resource, amount, comment, start, end, auth in creations:
            created = create_allocation(
                session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=amount,
                start_date=start,
                end_date=end,
                user_id=None,
                comment=comment,
            )
            if auth:
                _mark_panel_authorised(session, created)

    return DispatchResult(status='processed', service='supplement', projcode=projcode)


def _mark_panel_authorised(session, allocation: Allocation) -> None:
    """Set ``auth_at_panel_mtg`` on the CREATE row ``create_allocation`` just wrote.

    ``create_allocation`` is the shared primitive and does not know about this column;
    reaching for the row it just added is cheaper and less invasive than widening its
    signature for one caller.
    """
    latest = max(allocation.transactions,
                 key=lambda t: (t.creation_time, t.allocation_transaction_id or 0),
                 default=None)
    if latest is not None:
        latest.auth_at_panel_mtg = True
        session.flush()


register('supplement', handle_supplement)
