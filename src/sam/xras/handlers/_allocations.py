"""Finding, dating and marking the allocations an action acts on.

The half of the shared vocabulary that touches the allocation graph rather than the
wire. Same reason for existing as :mod:`._fields`: these lived in whichever handler
needed them first, so Adjustment imported six names from Supplement and Update imported
from all three of its predecessors.

WARNING: **Two account lookups live here and they are deliberately asymmetric.** Read them
together before using either:

- :func:`account_is_active` filters hard — inactive project or decommissioned resource
  and the account is skipped. Extension uses it.
- :func:`account_for_resource` is *unfiltered*, matching ``Project.getAccount(name)``
  case-insensitively over **all** accounts. Supplement, Adjustment and Update use it.

So a Supplement lands on an account whose resource is decommissioned, where an Extension
would skip it. That is legacy's behavior on both sides. Until this module existed the
separation-by-file was doing part of the work of keeping them apart; now the warning has
to.

WARNING: **``auth_at_panel_mtg`` reaches a row two different ways**, and the difference is why
an Adjustment silently lost the flag for a whole sprint. A CREATE row is marked *after
the fact* by :func:`mark_panel_authorized`; a SUPPLEMENT row carries it as a parameter
into ``supplement_allocation``. One concept, two mechanisms, and no compiler to tell you
which one applies.
"""

import logging
from datetime import datetime
from typing import Optional, Tuple

from sam.accounting.allocations import Allocation
from sam.base import normalize_end_date
from sam.projects.projects import Project
from sam.resources.resources import Resource

from .. import errors as e
from ..errors import ActionErrors
from ..extractors import select_allocation_type_mapped
from ..wire import get_field

logger = logging.getLogger(__name__)

__all__ = [
    'account_is_active',
    'effective_end_date',
    'latest_allocation',
    'account_for_resource',
    'new_allocation_end_date',
    'clamp_start_to_commission',
    'auth_at_panel_meeting',
    'mark_panel_authorised',
    'create_window_from_project_history',
    'create_window_from_action_dates',
]

#: ``AllocationTypeIdExtractor``'s two panel-authorized types. ``getAuthAtPanelMeeting()``
#: is ``true`` iff the resolved type is one of these.
_PANEL_AUTHORISED = frozenset({'CSL', 'CHAP'})


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

    WARNING: **Not ``Account.is_active``.** SAM's hybrid on this model comes from
    ``SoftDeleteMixin`` and means "not deleted", which is a different question
    entirely — using it here would extend allocations on decommissioned resources and
    on inactive projects. The house rule (CLAUDE.md § 5) is to prefer the hybrid, and
    this is the case where doing so would be wrong; the composite is built from the
    other models' documented predicates rather than from raw columns.

    WARNING: **``!creationTime.after(now)`` is deliberately not ported.** It compares two
    clocks that are not the same clock: ``account.creation_time`` carries
    ``server_default=CURRENT_TIMESTAMP`` and resolves in the **MySQL server's**
    timezone, which is UTC in the dev and CI containers, while ``now`` is
    ``datetime.now()`` in SAM's naive-Mountain convention. Measured, today, against the
    test container: ``NOW()`` returns 12:45 while Python returns 06:45 — a six-hour
    skew, in the direction that makes every account created in the last six hours look
    like it was created in the future.

    The conjunct can only ever *exclude*, so honoring it under skew means an Extension
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

    WARNING: An allocation whose **effective** end date is null is returned *immediately*,
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


def account_for_resource(project: Project, resource: Resource):
    """``Project.getAccount(resourceName)`` — a scan over **all** accounts.

    WARNING: Deliberately unfiltered. Extension's ``account.isActive()`` gate does not apply
    here, so a supplement lands on an account whose project is inactive or whose
    resource is decommissioned. Matching on the resource *name*, case-insensitively
    (``Account.isForResource`` uses ``equalsIgnoreCase``), rather than on the id —
    because that is the join legacy makes, and the two can disagree if two resource
    rows ever share a name. See this module's header for the asymmetry with
    :func:`account_is_active`.
    """
    wanted = (resource.resource_name or '').casefold()
    for account in project.accounts:
        if account.resource is not None and \
                (account.resource.resource_name or '').casefold() == wanted:
            return account
    return None


def new_allocation_end_date(project: Project,
                            start_date: datetime) -> Optional[datetime]:
    """``findLatestProjectEndDateForNewAllocation`` — contract first, then allocation.

    Latest **contract** end date if it is not before *start_date*; else the latest
    **allocation** end date under the same test; else ``None``, which the caller turns
    into ``All contract and allocation end dates are null or past for project [%s]``.

    WARNING: Kept bug-for-bug: the create branch derives its window from *today* and the
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


def clamp_start_to_commission(resource, start: datetime) -> datetime:
    """Push an allocation start forward to the resource's commission date.

    WARNING: **Silent, and deliberately so.** ``DefaultAddAllocationToProjectCommand`` clamps
    an early start with no report — the allocation simply begins later than XRAS asked.
    The *end* side is the opposite: an end at or before the commission date raises
    ``End date of allocation (%s) must be after commission date of resource(%s).``
    (note the missing space before the parenthesis, reproduced in
    :mod:`sam.xras.errors`) as an ``IllegalStateException``, which is not observer-
    reported and so becomes a 500 in legacy.

    This is new behavior with no precedent elsewhere in this repo, so it is isolated
    here rather than pushed into ``create_allocation`` — the operator-facing allocation
    flows should keep rejecting a bad start rather than quietly moving it.
    """
    commission = getattr(resource, 'commission_date', None)
    if commission is not None and start < commission:
        logger.info(
            'XRAS allocation start %s precedes %s commissioning (%s); clamping forward',
            start.date(), resource.resource_name, commission.date())
        return commission
    return start


def auth_at_panel_meeting(session, action) -> bool:
    """``getAuthAtPanelMeeting()`` — true iff the allocation type is CSL or CHAP.

    WARNING: The branch is **inverted** from what you would expect
    (``ProjectAllocationActionCommandsFactoryBase:96-114``): when the payload *carries*
    an ``allocationType`` it runs the eleven-strategy chain; when it does **not**, it
    reads the *existing project's* stored type and looks that up by name. Both arms are
    reproduced. Set on 1,264 of the 3,203 integration-written SUPPLEMENT rows in
    production, so it is not vestigial.

    WARNING: **Call this during assembly, before any transaction opens.** The second arm
    reads ``project.allocation_type``, and Update *writes* that column — through
    ``project.update()``, which flushes. Evaluating it lazily from inside the execute
    phase would read back the type the action just installed rather than the one the
    project had when it arrived. Nothing catches that today: no test changes
    ``allocationType`` on an Update whose resources take the add branch.

    WARNING: **Only the first arm consults the ``opportunityId`` map, and that is not an
    oversight.** This function re-derives the pair independently of
    ``resolve_allocation_type``, so if it kept calling the *pure* chain a project's
    allocation type could come from the map while its transactions'
    ``auth_at_panel_mtg`` flag came from the ladder — inconsistent rows, written,
    silently. The second arm needs no such change: it reads the type already stored
    on the project, which the mapped resolver is what wrote. Pointing it at the map
    would change behavior for payloads that omit ``allocationType`` entirely, which
    is a different question from this one.
    """
    if get_field(action, 'allocationType'):
        parms = select_allocation_type_mapped(session, action)
        derived = parms is not None and parms.allocation_type in _PANEL_AUTHORISED
    else:
        projcode = (get_field(action, 'requestNumber') or '').strip()
        project = Project.get_by_projcode(session, projcode) if projcode else None
        stored = (getattr(project.allocation_type, 'allocation_type', None)
                  if project else None)
        derived = stored in _PANEL_AUTHORISED

    # The wire names the reviewing panel outright in panels[] — CHAP in exactly
    # the payloads where this flag matters. It can only ADD authorization (it
    # reaches the pairs the ladder cannot, e.g. an opportunityName variant the
    # strategies miss); a wire/derivation disagreement is logged, and the
    # derived True is never withdrawn — the stored-type arm's CSL answer has no
    # wire counterpart, so trusting a non-CHAP panel to revoke it would rewrite
    # legacy's accounting convention on real Supplement rows.
    wire = _panel_authorised_on_the_wire(action)
    if wire is not None and wire != bool(derived):
        logger.warning(
            'XRAS panels[] disagreement for %s: primary panel says %s, the '
            'type derivation says %s; authorizing if either does',
            get_field(action, 'requestNumber'), wire, bool(derived))
    return bool(derived) or bool(wire)


def _panel_authorised_on_the_wire(action):
    """``panels[]``'s own answer: the primary panel's abbr, against the set.

    ``True``/``False`` when a primary panel is named; ``None`` when ``panels[]``
    is empty or flags none primary (NOT ``panels[0]`` — Large opportunities
    carry two). CHAP is the only panel-authorized abbr observed on the wire;
    CSL exists solely as a stored legacy type.
    """
    for panel in get_field(action, 'panels') or ():
        if get_field(panel, 'isPrimary'):
            abbr = str(get_field(panel, 'abbr') or '').strip()
            return abbr in _PANEL_AUTHORISED
    return None


def mark_panel_authorised(session, allocation) -> None:
    """Set ``auth_at_panel_mtg`` on the CREATE row ``create_allocation`` just wrote.

    ``create_allocation`` is the shared primitive and does not know about this column;
    reaching for the row it just added is cheaper and less invasive than widening its
    signature for one caller.

    WARNING: This is the *create* mechanism only. A SUPPLEMENT row gets the same flag by
    passing ``auth_at_panel_mtg=`` into ``supplement_allocation``, and an ADJUSTMENT row
    does not get it at all — ``buildAdjustAllocationCommand`` never sets it. See this
    module's header.
    """
    latest = max(allocation.transactions,
                 key=lambda t: (t.creation_time, t.allocation_transaction_id or 0),
                 default=None)
    if latest is not None:
        latest.auth_at_panel_mtg = True
        session.flush()


def create_window_from_project_history(
        project: Project, projcode: str,
        errs: ActionErrors) -> Optional[Tuple[datetime, datetime]]:
    """Create-branch dates for **Supplement and Adjustment**: today + project history.

    Returns ``(start, end)`` with *end* already normalized to 23:59:59, or ``None``
    having reported ``All contract and allocation end dates are null or past for
    project [%s]``.

    WARNING: One of **two** create policies, and they must not converge — see
    :func:`create_window_from_action_dates` for the other. This one never reads the
    action's own ``actionBeginDate``/``actionEndDate``, which is legacy's behavior and
    is kept bug-for-bug (100% of Supplement traffic succeeds under it).

    Named rather than inlined because it *was* inlined, twice, and the thirty-line
    duplication around it is where Adjustment's ``auth_at_panel_mtg`` went missing. Two
    call sites of one function cannot drift; two copies of thirty lines can, and did.
    """
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = new_allocation_end_date(project, start)
    if end is None:
        errs.report(e.all_end_dates_null_or_past(projcode))
        return None
    return start, normalize_end_date(end)


def create_window_from_action_dates(
        resource, begin: datetime, end: datetime,
        errs: ActionErrors) -> Optional[Tuple[datetime, datetime]]:
    """Create-branch dates for **New and Update**: the action's dates, start clamped.

    Returns ``(start, end)`` or ``None`` having reported
    ``End date of allocation (%s) must be after commission date of resource(%s).``

    Legacy raises ``IllegalStateException`` on the refusal, which escapes the observer
    and becomes a 500 with no diagnostic. Reported instead — the same refusal, in a form
    an operator can act on.

    WARNING: Takes the dates **already parsed**. That is not a convenience: ``new.py`` parses
    both above its resource loop so date errors precede resource errors in the 422 body,
    and the body's order is asserted in ten test modules. A version of this function
    that parsed the action itself would silently reorder every multi-resource rejection.
    """
    start = clamp_start_to_commission(resource, begin)
    if end <= start:
        errs.report(e.allocation_end_before_commission(
            end.strftime('%Y-%m-%d'), resource.resource_name))
        return None
    return start, end
