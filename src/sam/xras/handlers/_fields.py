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

⚠️ **Report order is part of the contract.** ``exc.messages`` is asserted as an *ordered*
list in ten test modules, because an XRAS administrator reads the 422 body top to bottom
and the order tracks the order of assembly. Callers control it; these functions only
decide *whether* to report. In particular ``new.py`` parses both dates **above** its
resource loop so date errors precede resource errors — a caller that pushed the parse
inside the loop would reorder the body without changing a line here.
"""

import logging
from datetime import datetime
from typing import List, Optional

from sam.base import normalize_end_date
from sam.integration.xras import XrasResourceRepositoryKeyResource
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

    Blank → ``Missing end date for allocation(s)``; unparseable → ``Could not convert
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


def plan_contracts(session, action, errs: ActionErrors) -> List:
    """Resolve every ``grants[]`` entry to a contract.

    ⚠️ ``grants: []`` is **not** an error — ``new_ncar4232_failed.json`` is an
    Educational allocation with no grant at all, and its failure was the mnemonic, not
    the missing contract. A project with no contract is legitimate.
    """
    contracts = []
    for grant in get_field(action, 'grants') or ():
        contract = resolve_contract(session, get_field(grant, 'grantNumber'), errs)
        if contract is not None:
            contracts.append(contract)
    return contracts
