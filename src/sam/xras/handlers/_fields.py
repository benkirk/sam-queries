"""One wire field in, one value out — reporting into an :class:`ActionErrors`.

Every function here reads a named field off an action (or off one entry of its
``resources[]`` array), converts it, and on failure reports the exact legacy string and
returns ``None``. None of them writes, and none of them raises: the accumulate-then-
check-once contract means a bad field must let the *rest* of assembly report its own
problems before ``raise_if_any()`` fires.

These lived in whichever handler needed them first — ``parse_action_end_date`` in
Extension, four more in Supplement, two in New — so ``update.py`` ended up importing
fifteen symbols from three sibling handlers and the import graph encoded build order
rather than domain structure. Nothing about the domain says Update depends on New.

WARNING: **Report order is part of the contract.** ``exc.messages`` is asserted as an *ordered*
list in ten test modules, because an XRAS administrator reads the 422 body top to bottom
and the order tracks the order of assembly. Callers control it; these functions only
decide *whether* to report. In particular ``new.py`` parses both dates **above** its
resource loop so date errors precede resource errors — a caller that pushed the parse
inside the loop would reorder the body without changing a line here.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sam.base import normalize_end_date
from sam.integration.xras import XrasResourceRepositoryKeyResource, lookup_request_override
from sam.resources.resources import Resource

from .. import errors as e
from ..errors import ActionErrors
from ..extractors import resolve_contract
from ..roster import normalize_username
from ..wire import get_field

logger = logging.getLogger(__name__)

__all__ = [
    'title',
    'abstract',
    'parse_action_begin_date',
    'parse_action_end_date',
    'transaction_amount',
    'resource_comment',
    'resolve_resource',
    'plan_contracts',
]

#: ``project.title`` is ``varchar(255)``; legacy truncates with
#: ``StringUtil.cleanText(requestTitle, 255)``.
_TITLE_WIDTH = 255


def title(action, errs: ActionErrors) -> Optional[str]:
    """``getTitle()`` — blank reports ``Missing title``, else cleaned and truncated."""
    raw = get_field(action, 'requestTitle')
    value = (raw or '').strip()
    if not value:
        errs.report(e.missing_title())
        return None
    return value[:_TITLE_WIDTH]


def abstract(action) -> Optional[str]:
    """``getAbstract()`` — blank becomes ``None`` rather than an empty string."""
    raw = (get_field(action, 'requestAbstract') or '').strip()
    return raw or None


def parse_action_begin_date(action, errs: ActionErrors) -> Optional[datetime]:
    """``getStartDate()`` — the mirror of :func:`parse_action_end_date`.

    Reports ``Missing begin date for allocation(s)`` / ``Could not convert begin date
    for allocation(s)``. Unlike the end date this is **not** moved to end of day.
    """
    raw = get_field(action, 'actionBeginDate')
    if raw is None or not str(raw).strip():
        errs.report(e.missing_date('begin'))
        return None
    try:
        return datetime.strptime(str(raw).strip(), '%Y-%m-%d')
    except ValueError:
        errs.report(e.could_not_convert_date('begin'))
        return None


def parse_action_end_date(action, errs: ActionErrors) -> Optional[datetime]:
    """``ProjectAllocationActionCommandsFactoryBase.getEndDate()``.

    Blank -> ``Missing end date for allocation(s)``; unparseable -> ``Could not convert
    end date for allocation(s)``. Two separate strings — § 3.4 of the reference doc
    collapses them into one slashed line, which is one of its seven errors.

    A valid date is returned at **end of day**, matching legacy's ``getDateAtEndOfDay``
    and SAM's own 23:59:59 end-date convention. The two agree, which is why
    :func:`sam.base.normalize_end_date` can do the work.
    """
    raw = get_field(action, 'actionEndDate')
    if raw is None or not str(raw).strip():
        errs.report(e.missing_date('end'))
        return None
    try:
        parsed = datetime.strptime(str(raw).strip(), '%Y-%m-%d')
    except ValueError:
        errs.report(e.could_not_convert_date('end'))
        return None
    return normalize_end_date(parsed)


def transaction_amount(wire_resource, errs: ActionErrors) -> Optional[float]:
    """``getTransactionAmount`` — blank reports, unparseable reports, else a float.

    WARNING: **A declared divergence lives here.** Legacy's caller then does
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


def resolve_resource(session, wire_resource, errs: ActionErrors) -> Optional[Resource]:
    """``resources[].resourceRepositoryKey`` -> a SAM resource, via
    ``xras_resource_repository_key_resource``.

    Reports ``No resource found in SAM corresponding to key %s`` — the *key* variant.
    The roster path has its own string naming the resource **name** instead; both can
    fire for one action, which is why they are separate builders.

    WARNING: **The field is ``resourceRepositoryKey``, not ``key``.** This read said ``key``
    for an entire sprint. No XRAS payload has ever carried that field — all six
    resource-bearing corpus fixtures send ``resourceRepositoryKey``, the schema declares
    it under that name, and unknown keys are dropped on load — so through the real
    pipeline the key was always ``None`` and every resource on every Supplement,
    Adjustment, New and Update reported ``No resource found in SAM corresponding to
    key `` with nothing after it.

    It survived because every test built its own ``resources[]`` entries as
    ``{'key': ...}``, including the oracle's re-targeting helper. The name is now
    pinned by ``tests/unit/test_xras_wire_vocabulary.py``, which checks the whole
    read-vocabulary against the schema rather than this one field.

    WARNING: Only **13** mapping rows exist and 11 active SAM resources have none, so this is
    a live failure mode rather than a defensive branch. An award citing Derecho's GPU
    partition or Gust fails here, and the fix is a data fix.

    WARNING: Legacy calls ``getResourceName`` **twice** per resource on some paths, so an
    unmapped key reports twice and collapses to one line in the accumulator. That is
    the dedup working as designed, and it is why the container is a set.
    """
    key = get_field(wire_resource, 'resourceRepositoryKey')
    row = None
    if key is not None:
        row = (session.query(XrasResourceRepositoryKeyResource)
               .filter(XrasResourceRepositoryKeyResource.resource_repository_key == key)
               .first())
    if row is None or row.resource is None:
        errs.report(e.no_resource_for_key('' if key is None else str(key)))
        return None
    return row.resource


def _grant_without_number(number: str, grant) -> str:
    """The warning for a ``grants[]`` entry that cannot name an award."""
    agency = str(get_field(grant, 'fundingAgency') or '').strip()
    claimed = number or str(get_field(grant, 'title') or '').strip()
    what = f'"{claimed}"' if claimed else 'entry'
    where = f' [{agency}]' if agency else ''
    return f'Supporting grant {what}{where} has no award number; no contract linked'


def _grant_contract_ignored(number: str) -> str:
    """The warning for a contract miss an operator override chose to ignore."""
    return f'Contract "{number}" not found; blocker ignored by operator, grant unlinked'


_GRANT_DETAIL_KEYS = {'fundingAgency': 'agency', 'title': 'title', 'piName': 'pi_name',
                      'beginDate': 'begin_date', 'endDate': 'end_date',
                      'isPending': 'is_pending'}


def plan_contracts(session, action, errs: ActionErrors) -> Tuple[List, Tuple[str, ...], List[dict]]:
    """Resolve ``grants[]`` to ``(contracts, warnings, unresolved)``, deduped by contract id.

    ``unresolved`` is the structured channel for the contract-blockers report:
    one entry per grant that reported a 422, carrying the wire's own title,
    agency, PI and dates so the create form can be seeded without a lookup.

    WARNING: ``grants: []`` is **not** an error — ``new_ncar4232_failed.json`` is an
    Educational allocation with no grant at all, and its failure was the mnemonic, not
    the missing contract. A project with no contract is legitimate.

    Two declared divergences from legacy (docs/plans/implemented/XRAS_DATA_MODEL_UPLIFT.md):
    a number that is empty or digit-free ("NSF Graduate Fellowship") cannot name an
    award, so it warns instead of hard-failing the action; and two entries resolving
    to one row (``2146709`` / ``AGS-2146709``) link it once —
    ``project_contract`` is UNIQUE per ``(project, contract)``, so legacy's second
    insert was an unhandled ``IntegrityError``.
    """
    # Operator escape hatch: an ignore_contract override for this request waives
    # the "Cannot find contract" 422. Misses then route to a warning, and their
    # reports land in a scratch bag that never reaches the action's errors — the
    # project is created with the erroneous grant simply unlinked.
    ignore_missing = lookup_request_override(
        session, get_field(action, 'requestId'), 'ignore_contract') is not None
    report_to = ActionErrors() if ignore_missing else errs

    contracts, warnings, unresolved, seen = [], [], [], set()
    for grant in get_field(action, 'grants') or ():
        number = str(get_field(grant, 'grantNumber') or '').strip()
        if not any(ch.isdigit() for ch in number):
            warnings.append(_grant_without_number(number, grant))
            continue
        failed: List[dict] = []
        contract = resolve_contract(session, number, report_to, unresolved=failed)
        # Wire detail for the create form. A literal tuple of names: the
        # vocabulary gate (test_xras_wire_vocabulary) resolves them from the loop.
        for entry in failed:
            for wire in ('fundingAgency', 'title', 'piName', 'beginDate', 'endDate',
                         'isPending'):
                entry[_GRANT_DETAIL_KEYS[wire]] = get_field(grant, wire)
            if ignore_missing:
                warnings.append(_grant_contract_ignored(number))
            else:
                unresolved.append(entry)
        if contract is not None and contract.contract_id not in seen:
            seen.add(contract.contract_id)
            contracts.append(contract)
    return contracts, tuple(warnings), unresolved
