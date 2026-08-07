"""
XRAS action-log query functions for SAM.

Read side of ``xras_action_log`` — the audit trail written by
``POST /api/xras/v1/actions`` (see ``webapp/api/xras/actions.py`` for the write
side and ``sam/integration/xras.py`` for the model). These back the Allocations
dashboard's XRAS page, its detail modal, and ``sam-admin xras``.

Functions:
    get_recent_xras_actions: filtered, sorted, paginated action rows
    count_recent_xras_actions: matching row count for the same filters
    summarize_xras_actions: rollup by status x action_type
    get_xras_pending_activation: XRAS-touched projects awaiting activation

Every function returns plain dicts rather than ORM instances. Display code —
Jinja templates and the CLI's ``rich`` renderers alike — takes dicts only, and
the same dict is what ``--format json`` emits, so the two renderings cannot
drift (``src/cli/README.md`` § *Adding New Commands*).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.projects.projects import Project

#: The five values ``xras_action_log.status`` may take, in lifecycle order.
#: ``status`` is a plain ``varchar(16)`` — this tuple is the vocabulary, and it is
#: what the page's filter dropdown and the summary rollup enumerate.
#:
#: ``processed`` is **unvalidated**: no handler exists yet, so nothing writes it.
#: It is listed because the UI must render it, not because it has been observed.
XRAS_ACTION_STATUSES = ('received', 'processed', 'manual', 'failed', 'replayed')

#: Action types seen on the wire, for the filter dropdown. Deliberately *not* a
#: constraint — ``XrasActionSchema`` applies no enum to ``actionType`` (no co-PI
#: role has ever been sampled, and the Supplement/Update spellings are unconfirmed),
#: so an unrecognised type must still list and still filter. Callers union this with
#: whatever ``DISTINCT action_type`` actually holds.
XRAS_ACTION_TYPES = ('New', 'Extension', 'Supplement', 'Update', 'Adjust', 'Transfer')

#: URL-facing sort key -> column. Mirrors ``ALLOCATION_TRANSACTION_SORT_COLUMNS``
#: in ``queries/allocations.py``: the dashboard whitelists ``sort_by`` against this
#: dict's keys and the query re-validates below (defence in depth — a raw column
#: name must never reach ``order_by``).
XRAS_ACTION_SORT_COLUMNS = {
    'received_time': XrasActionLog.received_time,
    'action_type': XrasActionLog.action_type,
    'request_number': XrasActionLog.request_number,
    'status': XrasActionLog.status,
    'http_status': XrasActionLog.http_status,
    'projcode_result': XrasActionLog.projcode_result,
    'processed_by': XrasActionLog.processed_by,
}


def _split_errors(error_messages: Optional[str]) -> List[str]:
    """``error_messages`` is newline-joined; give callers the ordered list back.

    The order is the contract — legacy accumulates every problem into an ordered
    ``LinkedHashSet`` and raises once, which is what lets an operator fix a request
    in one pass instead of five. Blank lines are dropped; nothing is re-sorted.
    """
    if not error_messages:
        return []
    return [line for line in error_messages.split('\n') if line.strip()]


def _apply_action_filters(
    query,
    *,
    action_log_id,
    action_type,
    status,
    request_number,
    projcode,
    remote_actor,
    processed_by,
    http_status,
    has_errors,
    replays_only,
    replay_of,
    start_date,
    end_date,
):
    """Apply the shared WHERE clauses.

    Scope arguments accept a scalar, a list, or ``None`` (no filter), matching the
    convention in ``queries/allocations.py``. Note there is no ``"TOTAL"`` sentinel
    here — that idiom belongs to the allocation-summary family, and this table has
    no rollup row to name.
    """
    def _in(column, value):
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            values = [v for v in value if v not in (None, '')]
            return column.in_(values) if values else None
        return column == value

    for clause in (
        _in(XrasActionLog.xras_action_log_id, action_log_id),
        _in(XrasActionLog.action_type, action_type),
        _in(XrasActionLog.status, status),
        _in(XrasActionLog.request_number, request_number),
        _in(XrasActionLog.projcode_result, projcode),
        _in(XrasActionLog.remote_actor, remote_actor),
        _in(XrasActionLog.processed_by, processed_by),
        _in(XrasActionLog.http_status, http_status),
        _in(XrasActionLog.replay_of_id, replay_of),
    ):
        if clause is not None:
            query = query.filter(clause)

    if start_date is not None:
        query = query.filter(XrasActionLog.received_time >= start_date)
    if end_date is not None:
        query = query.filter(XrasActionLog.received_time <= end_date)

    if has_errors is True:
        query = query.filter(XrasActionLog.error_messages.isnot(None),
                             XrasActionLog.error_messages != '')
    elif has_errors is False:
        query = query.filter((XrasActionLog.error_messages.is_(None))
                             | (XrasActionLog.error_messages == ''))

    if replays_only is True:
        query = query.filter(XrasActionLog.replay_of_id.isnot(None))
    elif replays_only is False:
        query = query.filter(XrasActionLog.replay_of_id.is_(None))

    return query


def get_recent_xras_actions(
    session: Session,
    *,
    action_log_id: Optional[Union[int, List[int]]] = None,
    action_type: Optional[Union[str, List[str]]] = None,
    status: Optional[Union[str, List[str]]] = None,
    request_number: Optional[Union[str, List[str]]] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    remote_actor: Optional[Union[str, List[str]]] = None,
    processed_by: Optional[Union[str, List[str]]] = None,
    http_status: Optional[Union[int, List[int]]] = None,
    has_errors: Optional[bool] = None,
    replays_only: Optional[bool] = None,
    replay_of: Optional[Union[int, List[int]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_payload: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    offset: int = 0,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Query XRAS action-log rows with flexible filters.

    Args:
        action_log_id: primary key(s) — the single-row lookup behind the detail modal.
        action_type: ``'New'`` / ``'Extension'`` / … (see ``XRAS_ACTION_TYPES``).
        status: one or more of ``XRAS_ACTION_STATUSES``.
        request_number: XRAS request number, which *is* the projcode for actions
            against an existing project and an ``NCAR####`` token for New.
        projcode: filter on ``projcode_result`` — the minted projcode, which
            diverges from ``request_number`` exactly on the New path.
        http_status: 200 / 400 / 422. ``status='failed'`` covers both 400 and 422,
            so this is how triage separates a malformed body from a rejected one.
        has_errors: ``True`` for rows carrying an error list, ``False`` for clean
            rows, ``None`` for no filter.
        replays_only: ``True`` for replay rows only, ``False`` for original posts
            only, ``None`` for both.
        start_date, end_date: inclusive bounds on ``received_time``.
        include_payload: when True, add ``raw_payload`` to each dict. **Off by
            default and it matters**: the payload is the request body verbatim and
            carries participant names, emails, phone numbers and grant-officer
            contacts. A list view has no business shipping ~3 KB of PII per row to
            a template, so the caller must ask — and the web caller only asks after
            checking ``Permission.MANAGE_XRAS``.
        sort_by: key in ``XRAS_ACTION_SORT_COLUMNS``. Defaults to ``received_time``.
        sort_dir: ``'asc'`` or ``'desc'``. Default ``'desc'`` — newest first.
        offset, limit: pagination.

    Returns:
        A list of dicts, one per row, with the keys:

        - ``action_log_id``, ``received_time``, ``remote_actor``
        - ``action_type``, ``request_number``, ``status``, ``http_status``
        - ``errors`` (ordered ``list[str]``, ``[]`` when clean)
        - ``projcode_result``, ``processed_time``, ``processed_by``
        - ``replay_of_id``, ``replay_count`` (how many replays this row has spawned)
        - ``request_is_project`` / ``result_is_project`` — whether that code
          actually resolves to a row in ``project`` (see below)
        - ``raw_payload`` only when ``include_payload=True``

    The two ``*_is_project`` flags exist because **``request_number`` is not always
    a projcode**: it is one for actions against an existing project, and an
    ``NCAR####`` request token for New. A UI that linked every ``request_number``
    to a project page would send an operator to a 404 on exactly the 21% of traffic
    that matters most. Resolved in one extra ``IN`` query per page rather than a
    join, so it costs one round trip bounded by ``limit``.
    """
    if sort_by is not None and sort_by not in XRAS_ACTION_SORT_COLUMNS:
        raise ValueError(
            f"Unknown sort_by={sort_by!r}; allowed: "
            f"{sorted(XRAS_ACTION_SORT_COLUMNS)}"
        )
    if sort_dir not in ('asc', 'desc'):
        raise ValueError(f"sort_dir must be 'asc' or 'desc', got {sort_dir!r}")

    sort_col = (XRAS_ACTION_SORT_COLUMNS[sort_by] if sort_by
                else XrasActionLog.received_time)

    # Replay fan-out as a correlated scalar rather than a second round trip: the
    # table renders a "replayed" chip per row, and N+1 on a 200-row page is the
    # kind of thing that only shows up once production has volume.
    replay_child = XrasActionLog.__table__.alias('replay_child')
    replay_count = (
        session.query(func.count(replay_child.c.xras_action_log_id))
        .filter(replay_child.c.replay_of_id == XrasActionLog.xras_action_log_id)
        .correlate(XrasActionLog)
        .scalar_subquery()
    )

    query = _apply_action_filters(
        session.query(XrasActionLog, replay_count.label('replay_count')),
        action_log_id=action_log_id,
        action_type=action_type,
        status=status,
        request_number=request_number,
        projcode=projcode,
        remote_actor=remote_actor,
        processed_by=processed_by,
        http_status=http_status,
        has_errors=has_errors,
        replays_only=replays_only,
        replay_of=replay_of,
        start_date=start_date,
        end_date=end_date,
    )

    order = sort_col.asc() if sort_dir == 'asc' else sort_col.desc()
    # Secondary key on the PK: received_time has one-second resolution and the
    # seeding loop posts four payloads inside one second, so without it paging
    # is not stable.
    query = query.order_by(order, XrasActionLog.xras_action_log_id.desc())

    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    rows = []
    for row, n_replays in query.all():
        item = {
            'action_log_id': row.xras_action_log_id,
            'received_time': row.received_time,
            'remote_actor': row.remote_actor,
            'action_type': row.action_type,
            'request_number': row.request_number,
            'status': row.status,
            'http_status': row.http_status,
            'errors': _split_errors(row.error_messages),
            'projcode_result': row.projcode_result,
            'processed_time': row.processed_time,
            'processed_by': row.processed_by,
            'replay_of_id': row.replay_of_id,
            'replay_count': n_replays or 0,
        }
        if include_payload:
            item['raw_payload'] = row.raw_payload
        rows.append(item)

    _annotate_project_existence(session, rows)
    return rows


def _annotate_project_existence(session, rows):
    """Set ``request_is_project`` / ``result_is_project`` on each row, in one query.

    ``request_number`` is a projcode for Extension/Supplement/Update and an
    ``NCAR####`` token for New, and nothing in the row distinguishes them — the
    only way to know is to ask whether a project by that name exists. Done for the
    whole page at once; a per-row lookup would be N+1 on the one table most likely
    to grow.
    """
    for item in rows:
        item['request_is_project'] = False
        item['result_is_project'] = False
    if not rows:
        return

    codes = {c for item in rows
             for c in (item['request_number'], item['projcode_result']) if c}
    if not codes:
        return

    known = {c for (c,) in session.query(Project.projcode)
             .filter(Project.projcode.in_(codes)).all()}
    for item in rows:
        item['request_is_project'] = item['request_number'] in known
        item['result_is_project'] = item['projcode_result'] in known


def count_recent_xras_actions(
    session: Session,
    *,
    action_log_id: Optional[Union[int, List[int]]] = None,
    action_type: Optional[Union[str, List[str]]] = None,
    status: Optional[Union[str, List[str]]] = None,
    request_number: Optional[Union[str, List[str]]] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    remote_actor: Optional[Union[str, List[str]]] = None,
    processed_by: Optional[Union[str, List[str]]] = None,
    http_status: Optional[Union[int, List[int]]] = None,
    has_errors: Optional[bool] = None,
    replays_only: Optional[bool] = None,
    replay_of: Optional[Union[int, List[int]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """Count rows matching the same filters as :func:`get_recent_xras_actions`.

    A separate query, not ``len(rows)`` — the caller is paginating, so ``rows`` is
    one page and the total is what the nav needs.
    """
    query = _apply_action_filters(
        session.query(func.count(XrasActionLog.xras_action_log_id)),
        action_log_id=action_log_id,
        action_type=action_type,
        status=status,
        request_number=request_number,
        projcode=projcode,
        remote_actor=remote_actor,
        processed_by=processed_by,
        http_status=http_status,
        has_errors=has_errors,
        replays_only=replays_only,
        replay_of=replay_of,
        start_date=start_date,
        end_date=end_date,
    )
    return query.scalar() or 0


def summarize_xras_actions(
    session: Session,
    *,
    action_type: Optional[Union[str, List[str]]] = None,
    status: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Roll the log up by status and by (status, action_type).

    One function serves the page's summary strip and ``sam-admin xras --summary``,
    so the two can never disagree about what "12 failed" means.

    **Every status in ``XRAS_ACTION_STATUSES`` appears in ``by_status``, including
    at zero.** An absent bucket reads as "not measured" rather than "none", which
    is the same reasoning as the contract audit's always-present check sections
    (``cli/contracts/builders.py``). ``by_type`` carries only observed pairs —
    enumerating the 5x6 cross product would be noise, since most cells are
    structurally impossible.

    Returns:
        ``{'total': int, 'by_status': {status: count},
           'by_type': [{'status', 'action_type', 'count'}, ...]}``
    """
    query = _apply_action_filters(
        session.query(
            XrasActionLog.status,
            XrasActionLog.action_type,
            func.count(XrasActionLog.xras_action_log_id).label('n'),
        ),
        action_log_id=None,
        action_type=action_type,
        status=status,
        request_number=None,
        projcode=None,
        remote_actor=None,
        processed_by=None,
        http_status=None,
        has_errors=None,
        replays_only=None,
        replay_of=None,
        start_date=start_date,
        end_date=end_date,
    ).group_by(XrasActionLog.status, XrasActionLog.action_type)

    by_status = {s: 0 for s in XRAS_ACTION_STATUSES}
    by_type: List[Dict[str, Any]] = []
    total = 0

    for row_status, row_type, n in query.all():
        total += n
        # A status outside the vocabulary would be a bug, not a filter miss — surface
        # it rather than dropping it on the floor.
        by_status[row_status] = by_status.get(row_status, 0) + n
        by_type.append({
            'status': row_status,
            'action_type': row_type,
            'count': n,
        })

    by_type.sort(key=lambda r: (-r['count'], r['status'], r['action_type'] or ''))
    return {'total': total, 'by_status': by_status, 'by_type': by_type}


def get_xras_pending_activation(
    session: Session,
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Projects an XRAS action touched that are still inactive.

    XRAS projects arrive ``active = 0`` **by design** and a human activates them.
    Legacy's trigger for that human is its success email; SAM has no mailer, so
    this view is the stand-in — which is what keeps SMTP deferred rather than a
    prerequisite for the ``POST /actions`` cutover.

    **Identification, and its limit.** There is no marker on ``Project`` saying
    "XRAS created this" — nothing in ``sam/projects/`` records provenance, and
    ``XrasActionView`` is an *outbound* reporting view derived from allocations,
    not a record of inbound posts. So a project qualifies here iff some
    ``xras_action_log`` row names it, via either:

    - ``projcode_result`` — the New path, where SAM minted the projcode; or
    - ``request_number`` — Extension/Supplement/Update, where XRAS sends the
      projcode *as* the request number.

    The consequence is worth stating plainly rather than discovering later: **this
    card sees only projects this table knows about.** It renders empty today
    (capture mode has created nothing) and grows exactly as the log grows. It does
    **not** retro-discover the 23 historical XRAS projects legacy created, and an
    empty card must not be read as "nothing pending" until SAM has been the system
    of record for a while.

    Returns:
        A list of dicts with ``projcode``, ``project_id``, ``title``,
        ``action_log_id``, ``action_type``, ``received_time``, ``status`` —
        one per project, carrying its most recent XRAS action.
    """
    # Both columns are projcode-shaped and either may name the project, so gather
    # candidates from each. A UNION in SQL would need the same OR-join anyway, and
    # this keeps the "which column matched" information available.
    candidates: Dict[str, Dict[str, Any]] = {}
    for column in (XrasActionLog.projcode_result, XrasActionLog.request_number):
        rows = (
            session.query(XrasActionLog, Project)
            .join(Project, Project.projcode == column)
            .filter(column.isnot(None))
            .filter(~Project.is_active)
            .order_by(XrasActionLog.received_time.desc(),
                      XrasActionLog.xras_action_log_id.desc())
            .all()
        )
        for action, project in rows:
            existing = candidates.get(project.projcode)
            # Keep the most recent action per project — an Extension that followed a
            # New is the one an operator should be looking at.
            if existing is not None and existing['received_time'] >= action.received_time:
                continue
            candidates[project.projcode] = {
                'projcode': project.projcode,
                'project_id': project.project_id,
                'title': project.title,
                'action_log_id': action.xras_action_log_id,
                'action_type': action.action_type,
                'received_time': action.received_time,
                'status': action.status,
            }

    pending = sorted(candidates.values(),
                     key=lambda r: r['received_time'], reverse=True)
    return pending[:limit] if limit is not None else pending
