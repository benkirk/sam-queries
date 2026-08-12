"""
The `requests/*` and `dates/requests/*` endpoints.

    GET /api/xras/v1/requests/request/{requestNumber}
    GET /api/xras/v1/requests/user/{username}
    GET /api/xras/v1/requests/role/{role}/{username}
    GET /api/xras/v1/dates/requests/{comma,separated,list}

All four share the `{message, result}` envelope, and the first three share the
`AccountingRequestResponse` body. Between them they saw **one** request in 30
days of production logs — they are contract obligations, not hot paths.

This module is the port of `RequestFactory` + `AccountingRequestResponse`.
SAM has no request entity; legacy derives one per (projcode, allocation end
date) group, which is what `xras_access.get_request_rows` reproduces.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import abort
from webapp.extensions import db

from sam.queries import xras_access

from . import bp, xras_api_required
from .serialize import omit_none, xras_response

#: `RequestServiceController.ROLES` — the URL segment is lowercase snake_case
#: and maps to the literal stored in the role view. `co_pi` is accepted and
#: always yields an empty result, because nothing ever emits 'CoPi'.
ROLE_SEGMENTS = {
    'pi': 'Pi',
    'co_pi': 'CoPi',
    'allocation_manager': 'AllocationManager',
}

#: `dates/requests` serialises `java.util.Date` with no date module configured,
#: so Jackson emits epoch millis. The values are DATE columns read by a JVM in
#: the server's zone, i.e. **local midnight** — verified on four samples, all of
#: which land exactly on 00:00 America/Denver. A fixed -6 offset would drift by
#: an hour for winter dates, so this must be a real zone.
_SERVER_TZ = ZoneInfo('America/Denver')

_QUANT = Decimal('0.1')


def _amount_string(value):
    """Render an amount as legacy's `String.format("%.1f", ...)` does.

    Java's `%f` rounds HALF_UP on the exact binary value of the double; Python's
    own float formatting rounds half-to-even. They agree except at exact `.x5`
    midpoints, so the conversion goes through `Decimal` to pin the rounding
    mode rather than leaving a silent, data-dependent difference.
    """
    if value is None:
        return None
    return str(Decimal(float(value)).quantize(_QUANT, rounding=ROUND_HALF_UP))


def _date_string(value):
    """`DateUtil.getDateAsString` — Joda `yyyy-MM-dd`.

    Several sources are `datetime` columns, and legacy truncates the time of
    day exactly like this.
    """
    return None if value is None else value.strftime('%Y-%m-%d')


def _epoch_millis(value):
    """A date as epoch milliseconds at server-local midnight.

    The driver hands back a `date` for a DATE column, which has no `tzinfo`, so
    it is widened to midnight before the zone is attached.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    return int(value.replace(tzinfo=_SERVER_TZ).timestamp() * 1000)


def _build_action(row, order_applied):
    """`Action` — NON_NULL, so `amount` and `endDate` disappear when absent.

    Which ones are absent tracks `actionType`: an `Extension` carries an end
    date but no amount, a `Supplemental` the reverse. Emitting both
    unconditionally would break parity on most responses.
    """
    return omit_none({
        'orderApplied': order_applied,
        'actionType': row.actionType,
        'amount': _amount_string(row.amount),
        'endDate': _date_string(row.endDate),
        'dateApplied': _date_string(row.dateApplied),
    })


def _build_allocation(row, action_rows):
    """`Allocation` — NON_NULL.

    `actionType`, `xrasActionId` and `xrasActionResourceId` exist on the Java
    class but `RequestFactory` never sets them, so they are never emitted;
    they are omitted here by simply not being built.

    `remainingAmount` is HPC-only and `resourceRepositoryKey` comes from a LEFT
    JOIN against the resource-key mapping table, so both drop out routinely —
    the latter is the unmapped-resource gap surfacing on the wire.
    """
    return omit_none({
        'allocationBeginDate': _date_string(row.allocationBeginDate),
        'allocationEndDate': _date_string(row.allocationEndDate),
        'allocatedAmount': _amount_string(row.allocatedAmount),
        'remainingAmount': _amount_string(row.remainingAmount),
        'resourceRepositoryKey': row.resourceRepositoryKey,
        'actions': [_build_action(a, i + 1)
                    for i, a in enumerate(action_rows)],
    })


def _build_request(row, request_type, allocations):
    """`Request` — NON_NULL, so `xrasActionIds` (never set) never appears.

    `fos` is always exactly one element with `isPrimary: true`; legacy builds it
    from `project.area_of_interest_id` with no null check, and `FieldOfScience`
    carries no NON_NULL, so both keys are always present.
    """
    return omit_none({
        'requestType': request_type,
        'requestBeginDate': _date_string(row.requestBeginDate),
        'requestEndDate': _date_string(row.requestEndDate),
        'allocationType': row.allocationType,
        'projectTitle': row.projectTitle,
        'projectId': row.projectId,
        'fos': [{'xrasFosTypeId': row.xrasFosTypeId, 'isPrimary': True}],
        'allocations': allocations,
    })


def _request_types(rows):
    """Reproduce `HibernateAccountingDao.setRequestTypes`.

    Everything is `Renewal`; then, per project, the row with the smallest
    `requestBeginDate` becomes `New`. The Java comparison is a strict
    `.after()`, so a tie keeps the incumbent — meaning the **first** row in
    result-set order wins, and that order is end-date ascending.

    Returns a list of type strings positionally aligned with *rows*.
    """
    earliest = {}
    for index, row in enumerate(rows):
        current = earliest.get(row.projectId)
        if current is None or rows[current].requestBeginDate > row.requestBeginDate:
            earliest[row.projectId] = index

    types = ['Renewal'] * len(rows)
    for index in earliest.values():
        types[index] = 'New'
    return types


def _accounting_response(projcodes):
    """Assemble `AccountingRequestResponse` for a set of projcodes.

    `projectIdLabel` is emitted as null: nothing in legacy ever assigns it, and
    `AccountingRequestResponse` carries no NON_NULL annotation.
    """
    projcodes = sorted(set(projcodes))
    if not projcodes:
        return {'projectIdLabel': None, 'masters': []}

    request_rows = xras_access.get_request_rows(db.session, projcodes)
    allocation_rows = xras_access.get_allocation_rows(db.session, projcodes)
    action_rows = xras_access.get_action_rows(db.session, projcodes)

    # Both groupings preserve their query's ORDER BY, which is the array order
    # in the response: allocations by start_date DESC, actions by creation_time.
    allocations_by_id = {row.allocationId: row for row in allocation_rows}
    actions_by_allocation = {}
    for row in action_rows:
        actions_by_allocation.setdefault(row.allocationId, []).append(row)

    ordered_allocation_ids = [row.allocationId for row in allocation_rows]
    types = _request_types(request_rows)

    masters = {}
    for row, request_type in zip(request_rows, types):
        # `allocationIds` is the group's membership list (a GROUP_CONCAT). It is
        # a membership filter only — the array order comes from the allocation
        # query. Legacy raises IllegalStateException -> 500 when an id here has
        # no allocation row; that cannot happen for us, because both come from
        # the same tables in the same transaction rather than three views.
        members = {int(i) for i in (row.allocationIds or '').split(',') if i}
        allocations = [
            _build_allocation(allocations_by_id[aid],
                              actions_by_allocation.get(aid, []))
            for aid in ordered_allocation_ids
            if aid in members
        ]
        master = masters.setdefault(
            row.projectId, {'requestNumber': row.projectId, 'requests': []})
        master['requests'].append(_build_request(row, request_type, allocations))

    # Legacy emits masters in Java HashMap bucket order over the projcode keys —
    # an artifact of its data structure, not of the data. Deliberate divergence:
    # we sort. See docs/xras/incoming/XRAS_REIMPLEMENTATION.md section 7.
    return {
        'projectIdLabel': None,
        'masters': [masters[code] for code in sorted(masters)],
    }


def _require_user(username):
    """`RequestServiceController.validateUser` — note the wording differs from
    `/people/{username}`'s 404 ('User x' vs 'username=x')."""
    if xras_access.get_person(db.session, username) is None:
        abort(404, f'User {username} not found')


@bp.route('/requests/request/<request_number>', methods=['GET'])
@xras_api_required()
def get_request(request_number):
    """`{requestNumber}` **is the projcode** — the derived request rows key on
    `project.projcode`, and the POST side does `projcode = trimToNull(...)`.

    An unknown value is a 200 with empty `masters`, not a 404.
    """
    return xras_response(_accounting_response([request_number]))


@bp.route('/requests/user/<username>', methods=['GET'])
@xras_api_required()
def get_requests_by_user(username):
    """Every project where the user is lead or admin."""
    _require_user(username)
    return xras_response(
        _accounting_response(xras_access.get_role_projcodes(db.session, username)))


@bp.route('/requests/role/<role>/<username>', methods=['GET'])
@xras_api_required()
def get_requests_by_role(role, username):
    """`co_pi` is valid and always empty — nothing ever emits the 'CoPi' literal.

    **Deliberate divergence:** legacy answers an unrecognised role with an
    `IllegalArgumentException` that lands in the catch-all handler, producing a
    500 carrying only an opaque timestamp. A bad path segment is a client error;
    we return 400 with a usable message. Zero traffic, and the same reasoning as
    the 422 decision for `POST /actions`.

    Note the role check runs *before* user validation, matching legacy.
    """
    mapped = ROLE_SEGMENTS.get(role.lower())
    if mapped is None:
        abort(400, f'Invalid role {role.lower()}')
    _require_user(username)
    return xras_response(_accounting_response(
        xras_access.get_role_projcodes(db.session, username, mapped)))


@bp.route('/dates/requests/<request_numbers>', methods=['GET'])
@xras_api_required()
def get_request_dates(request_numbers):
    """Begin/end span per projcode, as **epoch milliseconds**.

    This is the only endpoint whose dates are not `yyyy-MM-dd` strings: its DTO
    holds raw `java.util.Date` and no date module is configured on the mapper.

    Legacy's `split(",")` does not trim, so `"A, B"` looks up `" B"` and
    silently misses. Reproduced — a client relying on it would see a shorter
    list, not an error.
    """
    projcodes = request_numbers.split(',')
    rows = xras_access.get_request_dates(db.session, projcodes)
    by_code = {row.requestNumber: row for row in rows}
    result = [
        {
            'requestNumber': by_code[code].requestNumber,
            'requestBeginDate': _epoch_millis(by_code[code].requestBeginDate),
            'requestEndDate': _epoch_millis(by_code[code].requestEndDate),
        }
        for code in projcodes if code in by_code
    ]
    return xras_response(result)
