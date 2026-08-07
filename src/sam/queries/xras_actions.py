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
    count_xras_dismissed_pending: how many of those are hidden by a dismissal
    get_latest_xras_action_id: provenance resolver for an activation event
    get_xras_activation_events: the append-only timeline for one project
    get_xras_pending_recipients: lead/admin addresses for the notify handoff

Every function returns plain dicts rather than ORM instances. Display code —
Jinja templates and the CLI's ``rich`` renderers alike — takes dicts only, and
the same dict is what ``--format json`` emits, so the two renderings cannot
drift (``src/cli/README.md`` § *Adding New Commands*).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog, XrasActivationEvent
from sam.projects.projects import Project

#: The five values ``xras_action_log.status`` may take, in lifecycle order.
#: ``status`` is a plain ``varchar(16)`` — this tuple is the vocabulary, and it is
#: what the page's filter dropdown and the summary rollup enumerate.
#:
#: ``processed`` is **unvalidated**: no handler exists yet, so nothing writes it.
#: It is listed because the UI must render it, not because it has been observed.
XRAS_ACTION_STATUSES = ('received', 'processed', 'manual', 'failed', 'replayed')

#: Action types on the wire, for the filter dropdown. This is legacy's own declared
#: vocabulary (``action/domain/model/Action.java``: ``// New, Extension, Supplement,
#: Transfer, Renewal, Adjustment, Advance``), corrected against real payloads.
#:
#: ⚠️  There is **no ``actionType`` of "Update"**. "Update" is a *handler*, not a
#: type: legacy dispatches on the pair ``(actionType, does the project exist)``, and
#: ``UpdateProjectActionService`` fires on ``New`` or ``Renewal`` when the projcode
#: already exists (``AddProjectActionService`` takes ``New`` when it does not). The
#: ``new_uwis0071_existing_ok.json`` fixture is that case. An earlier version of this
#: tuple listed ``'Update'`` and ``'Adjust'``, neither of which XRAS has ever sent.
#:
#: Deliberately *not* a constraint — ``XrasActionSchema`` applies no enum to
#: ``actionType`` (no co-PI role has ever been sampled, and Transfer / Renewal /
#: Advance still have zero samples), so an unrecognised type must still list and
#: still filter. Callers union this with whatever ``DISTINCT action_type`` holds.
XRAS_ACTION_TYPES = ('New', 'Renewal', 'Extension', 'Supplement',
                     'Transfer', 'Adjustment', 'Advance')

#: Wire spellings that mean the same handler, ``alias -> canonical``.
#:
#: XRAS sends ``actionType: "Adjustment"`` (measured — see
#: ``adjustment_uwis0064_manual.json``), but legacy's
#: ``AdjustProjectActionService.isServiceable`` tests ``equals("Adjust")``. The two
#: never match, so that handler has never once fired and every Adjustment falls
#: through ``ProjectActionServiceSelector`` to the manual-email fallback. Nothing has
#: shipped here yet, so SAM accepts **both** spellings rather than reproducing the
#: mismatch (see ``docs/plans/XRAS_REIMPLEMENTATION.md`` § 9, legacy defect 4).
#:
#: This is a **read-side** concern only. ``xras_action_log.action_type`` always
#: records what actually arrived, verbatim — the audit trail's whole job.
XRAS_ACTION_TYPE_ALIASES = {'Adjust': 'Adjustment'}


def canonical_action_type(value: Optional[str]) -> Optional[str]:
    """Fold an alias spelling onto its canonical one. ``None`` and unknowns pass through."""
    return XRAS_ACTION_TYPE_ALIASES.get(value, value)


def expand_action_types(value):
    """Widen an action-type filter to cover every spelling of the types requested.

    Filtering for ``'Adjustment'`` must return rows recorded as ``'Adjust'`` too,
    since they are the same action (see :data:`XRAS_ACTION_TYPE_ALIASES`). Symmetric
    by construction — each request is canonicalised *before* it is expanded, so
    asking by either spelling yields both. Accepts a scalar, an iterable or ``None``,
    and preserves that shape so it can sit directly in front of the ``_in`` helper.
    """
    if value is None:
        return None

    scalar = isinstance(value, str)
    requested = [value] if scalar else list(value)

    widened = []
    for wanted in requested:
        canonical = canonical_action_type(wanted)
        for spelling in (canonical, *(a for a, c in XRAS_ACTION_TYPE_ALIASES.items()
                                      if c == canonical)):
            if spelling not in widened:
                widened.append(spelling)

    # A single type with no aliases stays a scalar so the emitted SQL is unchanged.
    return widened[0] if scalar and len(widened) == 1 else widened

#: The local XRAS request-number token family — what XRAS sends as
#: ``requestNumber`` for a New action, before any projcode exists. At NCAR these
#: look like ``NCAR4253``. A different site re-points these two names and nothing
#: else; ``startswith`` takes a tuple, so a family of prefixes costs nothing.
#:
#: ⚠️  **DISPLAY ONLY. These must never decide whether a request number is a
#: projcode.** :func:`_annotate_project_existence` asks the *database*, and has
#: to: a token is projcode-**shaped**. Measured against real data, projcodes are
#: ``AAAA####`` (``UCUB0166``, ``UBOI0007``, ``NACD0009``) and ``NCAR4232`` is the
#: same eight-character shape, so no prefix or shape rule can tell them apart —
#: and a site holding a projcode that begins ``NCAR`` would have it misclassified
#: outright. ``test_a_projcode_shaped_like_a_request_token_is_still_a_project``
#: exists to fail the moment someone "simplifies" that lookup into a match.
#:
#: What they are legitimately for: telling an *unresolvable but expected* request
#: number (a New action whose project does not exist yet — normal) apart from an
#: *unrecognised* one (an Extension naming a projcode that is not in SAM — a
#: deleted or renamed project, or a mis-sent payload), and seeding the filter
#: box's example.
XRAS_REQUEST_TOKEN_PREFIXES = ('NCAR',)
XRAS_REQUEST_TOKEN_EXAMPLE = 'NCAR4253'

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
        # Widened here, once, so the list query, the count and the summary rollup
        # cannot disagree about what "Adjustment" selects.
        _in(XrasActionLog.action_type, expand_action_types(action_type)),
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
            Alias spellings are folded in, so ``'Adjustment'`` also selects rows
            recorded as ``'Adjust'`` (``XRAS_ACTION_TYPE_ALIASES``).
        status: one or more of ``XRAS_ACTION_STATUSES``.
        request_number: XRAS request number, which *is* the projcode for actions
            against an existing project and a request token for a New action that
            mints one. Note ``New`` does **not** imply a token: a New action against
            an existing project carries that project's projcode (measured — see
            ``new_uwis0071_existing_ok.json``), which is exactly the case legacy
            routes to ``UpdateProjectActionService``.
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
        - ``request_is_token`` — whether ``request_number`` looks like one of the
          site's request tokens (``XRAS_REQUEST_TOKEN_PREFIXES``). A display hint
          only; it takes no part in the ``*_is_project`` decision.
        - ``raw_payload`` only when ``include_payload=True``

    The two ``*_is_project`` flags exist because **``request_number`` is not always
    a projcode**: it is one for actions against an existing project, and a request
    token (``NCAR####`` at this site) for a New action that mints one — and only the
    database can tell those apart, since ``actionType`` alone cannot (a New action
    against an existing project sends a projcode). A UI that linked every
    ``request_number`` to a project page would send an operator to a 404 on exactly the 21% of traffic
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
    """Set ``request_is_project`` / ``result_is_project`` / ``request_is_token``.

    ``request_number`` is a projcode for Extension/Supplement/Update and a request
    token for New (``NCAR####`` at this site — see
    ``XRAS_REQUEST_TOKEN_PREFIXES``), and **nothing in the row distinguishes
    them**: the two are the same shape. The only way to know is to ask whether a
    project by that name exists, which is what this does — once for the whole
    page, because a per-row lookup would be N+1 on the table most likely to grow.

    ``request_is_token`` is a *separate*, weaker signal and is **not** part of
    that decision. It only says the value looks like one of the site's request
    tokens, which is what lets a caller tell "no project yet, as expected for a
    New action" apart from "names a projcode SAM does not have" — the second is
    worth an operator's attention and the first is not.
    """
    for item in rows:
        item['request_is_project'] = False
        item['result_is_project'] = False
        item['request_is_token'] = bool(
            item['request_number']
            and item['request_number'].startswith(XRAS_REQUEST_TOKEN_PREFIXES))
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
    request_number: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Roll the log up by status, by action type, and by the two together.

    One function serves the page's facet chips and ``sam-admin xras --summary``,
    so the two can never disagree about what "12 failed" means.

    **Every status in ``XRAS_ACTION_STATUSES`` appears in ``by_status``, including
    at zero.** An absent bucket reads as "not measured" rather than "none", which
    is the same reasoning as the contract audit's always-present check sections
    (``cli/contracts/builders.py``). ``by_type`` carries only observed pairs —
    enumerating the 5x6 cross product would be noise, since most cells are
    structurally impossible.

    ``by_action_type`` **keeps its ``None`` key**, which is a real bucket: a body
    that would not parse has no action type, and dropping it here would stop
    ``by_action_type`` reconciling with ``total``. Callers rendering it as a
    *filter* control must skip ``None`` themselves — "action_type IS NULL" is not
    expressible through the filter form — the same rule the jobs facet strip
    applies via ``rejectattr('value', 'none')``.

    On **faceting**: to use this for chips that double as switchers, call it once
    per dimension with that dimension's own filter omitted. Otherwise filtering to
    ``status='failed'`` drives every other status count to zero and the chips
    become dead ends rather than a way to move between statuses. See
    ``webapp/dashboards/allocations/blueprint.py::xras_fragment``.

    Returns:
        ``{'total': int,
           'by_status': {status: count},
           'by_action_type': {action_type|None: count},
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
        request_number=request_number,
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
    by_action_type: Dict[Optional[str], int] = {}
    total = 0

    merged_pairs: Dict[Any, int] = {}
    for row_status, row_type, n in query.all():
        total += n
        # A status outside the vocabulary would be a bug, not a filter miss — surface
        # it rather than dropping it on the floor.
        by_status[row_status] = by_status.get(row_status, 0) + n
        # Alias spellings are one logical type, so they are one bucket and one chip —
        # otherwise 'Adjust' and 'Adjustment' render as two chips that filter
        # identically. GROUP BY runs on the raw column; folding happens here.
        row_type = canonical_action_type(row_type)
        by_action_type[row_type] = by_action_type.get(row_type, 0) + n
        key = (row_status, row_type)
        merged_pairs[key] = merged_pairs.get(key, 0) + n

    by_type = [{'status': s, 'action_type': t, 'count': n}
               for (s, t), n in merged_pairs.items()]
    by_type.sort(key=lambda r: (-r['count'], r['status'], r['action_type'] or ''))
    return {
        'total': total,
        'by_status': by_status,
        'by_action_type': by_action_type,
        'by_type': by_type,
    }


def _activation_state(
    session: Session,
    project_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Latest event of each type, plus the comment count, per project.

    One grouped query over ``xras_activation_event`` for the whole page, joined
    in memory by the caller — not N+1, and not a correlated subquery per row.
    The ``(project_id, creation_time)`` index serves it.

    Ties on ``creation_time`` break by id descending: the column is DATETIME with
    one-second resolution, and two events a second apart in the same click burst
    are entirely possible.
    """
    if not project_ids:
        return {}

    rows = (
        session.query(XrasActivationEvent)
        .filter(XrasActivationEvent.project_id.in_(project_ids))
        .order_by(XrasActivationEvent.creation_time.asc(),
                  XrasActivationEvent.xras_activation_event_id.asc())
        .all()
    )

    state: Dict[int, Dict[str, Any]] = {}
    for event in rows:
        # Ascending order means a later row simply overwrites an earlier one, so
        # what survives per key is the latest.
        per_project = state.setdefault(event.project_id, {'comment_count': 0})
        if event.event_type == 'comment':
            per_project['comment_count'] += 1
        else:
            per_project[event.event_type] = event
    return state


def get_xras_pending_activation(
    session: Session,
    *,
    limit: Optional[int] = None,
    include_dismissed: bool = False,
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

    **Derived operator state.** Rows also carry the current state of the worklist,
    computed from ``xras_activation_event`` rather than stored. Nothing here is a
    boolean column; every state is a timestamp compared against ``received_time``,
    which is already the most recent action naming the project::

        hidden from the card  iff  latest('dismissed')
                                       > MAX(received_time, latest('restored'))
        "marked notified"     iff  latest('notified')  > received_time

    That single rule is both the anti-spam mechanism and the re-open mechanism: a
    dismissed project **reappears** when a new Extension arrives (new information
    — the operator should look again), while a notified one stays quiet until
    something actually changes, and goes stale the moment it does. A stored
    boolean gets both wrong. See ``sam.integration.xras.XrasActivationEvent``.

    Args:
        session: the session to query.
        limit: display cap, applied after sorting, in Python.
        include_dismissed: when True, hidden rows are returned too, flagged
            ``dismissed=True``. The card's "Show dismissed" toggle needs this —
            without it a dismissal is unrecoverable until a new action arrives,
            which may be never.

    Returns:
        A list of dicts with ``projcode``, ``project_id``, ``title``,
        ``action_log_id``, ``action_type``, ``received_time``, ``status`` —
        one per project, carrying its most recent XRAS action — plus the derived
        ``dismissed``, ``dismissed_time``, ``dismissed_by``, ``notified_time``,
        ``notified_by``, ``notified_stale`` and ``comment_count``.
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

    state = _activation_state(session, [c['project_id'] for c in candidates.values()])

    pending: List[Dict[str, Any]] = []
    for row in candidates.values():
        events = state.get(row['project_id'], {})
        dismissed_ev = events.get('dismissed')
        restored_ev = events.get('restored')
        notified_ev = events.get('notified')

        # A dismissal is superseded by whichever came later: a fresh XRAS action
        # (new information) or an explicit Restore (the operator undoing it).
        supersedes = row['received_time']
        if restored_ev is not None and restored_ev.creation_time > supersedes:
            supersedes = restored_ev.creation_time
        is_dismissed = (dismissed_ev is not None
                        and dismissed_ev.creation_time > supersedes)

        if is_dismissed and not include_dismissed:
            continue

        # Ages are computed here, as timedeltas, because ``fmt_ago`` takes an
        # elapsed delta rather than a timestamp. Doing the subtraction in the
        # template would put `datetime.now()` in Jinja, where it is untestable.
        now = datetime.now()
        row.update({
            'dismissed': is_dismissed,
            'dismissed_time': dismissed_ev.creation_time if is_dismissed else None,
            'dismissed_by': dismissed_ev.created_by if is_dismissed else None,
            'dismissed_reason': dismissed_ev.comment if is_dismissed else None,
            'notified_time': notified_ev.creation_time if notified_ev else None,
            'notified_age': (now - notified_ev.creation_time) if notified_ev else None,
            'notified_by': notified_ev.created_by if notified_ev else None,
            # Stale means "we told them, then the situation changed" — the button
            # becomes "Mark notified again" rather than staying quiet.
            'notified_stale': (notified_ev is not None
                               and notified_ev.creation_time <= row['received_time']),
            'comment_count': events.get('comment_count', 0),
        })
        pending.append(row)

    pending.sort(key=lambda r: r['received_time'], reverse=True)
    return pending[:limit] if limit is not None else pending


def count_xras_dismissed_pending(session: Session) -> int:
    """How many pending rows are currently hidden by a dismissal.

    Feeds the card's empty state and its "Show dismissed" toggle. Without a
    truthful count, "no rows" reads as "all clear" when it may mean "all
    dismissed" — the same honesty problem the empty-state copy already solves for
    capture mode.
    """
    return sum(1 for row in get_xras_pending_activation(
        session, include_dismissed=True) if row['dismissed'])


def get_latest_xras_action_id(
    session: Session,
    project_id: int,
) -> Optional[int]:
    """The most recent ``xras_action_log`` row naming *project_id*, or None.

    Provenance for ``xras_activation_event.xras_action_log_id``. Lives here
    rather than in a route because it reuses the ``projcode_result`` OR
    ``request_number`` join, which is the subtlest logic in this feature and must
    not be re-spelled anywhere else.
    """
    project = session.get(Project, project_id)
    if project is None:
        return None

    row = (
        session.query(XrasActionLog)
        .filter((XrasActionLog.projcode_result == project.projcode)
                | (XrasActionLog.request_number == project.projcode))
        .order_by(XrasActionLog.received_time.desc(),
                  XrasActionLog.xras_action_log_id.desc())
        .first()
    )
    return row.xras_action_log_id if row else None


def get_xras_activation_events(
    session: Session,
    project_id: int,
) -> List[Dict[str, Any]]:
    """The append-only operator timeline for one project, newest first.

    ⚠️ Rows carry ``notified_to`` — project lead and admin contact details. The
    caller must gate this on ``Permission.MANAGE_XRAS``, the same authority that
    gates the raw payload, not on ``VIEW_XRAS``.
    """
    rows = (
        session.query(XrasActivationEvent)
        .filter(XrasActivationEvent.project_id == project_id)
        .order_by(XrasActivationEvent.creation_time.desc(),
                  XrasActivationEvent.xras_activation_event_id.desc())
        .all()
    )
    now = datetime.now()
    return [{
        'event_id': r.xras_activation_event_id,
        'event_type': r.event_type,
        'comment': r.comment,
        'notified_to': r.notified_to,
        'action_log_id': r.xras_action_log_id,
        'created_by': r.created_by,
        'creation_time': r.creation_time,
        # ``fmt_ago`` takes an elapsed timedelta, not a timestamp.
        'age': now - r.creation_time,
    } for r in rows]


def get_xras_pending_recipients(
    session: Session,
    project_ids: List[int],
) -> Dict[int, List[Dict[str, str]]]:
    """Lead and admin contact details for the notify handoff, per project.

    ⚠️ **Deliberately not folded into** :func:`get_xras_pending_activation`.
    Doing so would push contact PII into every caller of that function, including
    the ``VIEW_XRAS`` render of the card. Keeping it separate lets the route ask
    for addresses only when the viewer holds ``MANAGE_XRAS``, so a view-source
    cannot leak what the page chose not to draw — the same route-level (not
    template-level) gate the raw-payload panel uses.

    Returns:
        ``{project_id: [{'name': ..., 'email': ..., 'role': 'lead'|'admin'}]}``.
        A project with neither on file maps to an empty list; the caller decides
        whether that blocks anything (it does not — an operator may have reached
        them out of band).
    """
    if not project_ids:
        return {}

    projects = (
        session.query(Project)
        .filter(Project.project_id.in_(project_ids))
        .all()
    )

    recipients: Dict[int, List[Dict[str, str]]] = {}
    for project in projects:
        people = []
        seen = set()
        for role, user in (('lead', project.lead), ('admin', project.admin)):
            if user is None:
                continue
            email = user.primary_email
            if not email or email in seen:
                continue
            seen.add(email)
            people.append({
                'name': user.full_name or user.username,
                'email': email,
                'role': role,
            })
        recipients[project.project_id] = people
    return recipients
