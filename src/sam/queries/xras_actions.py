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
    get_observed_action_types: every action_type present, aliases folded
    get_projects_by_ids: the Project rows for a set of ids

The pending-activation worklist is a different table and lives in
``sam.queries.xras_activation``; it imports :func:`action_names_project` and
``_LATEST_ACTION_ORDER`` from here, which is the one thing the two halves share.

Every function returns plain dicts rather than ORM instances. Display code —
Jinja templates and the CLI's ``rich`` renderers alike — takes dicts only, and
the same dict is what ``--format json`` emits, so the two renderings cannot
drift (``src/cli/README.md`` § *Adding New Commands*).
"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.projects.projects import Project

#: The values ``xras_action_log.status`` may take, in lifecycle order.
#: ``status`` is a plain ``varchar(16)`` — this tuple is the vocabulary, and it is
#: what the page's filter dropdown and the summary rollup enumerate. Adding a
#: member therefore costs **no DDL**, which is why ``unmapped`` could be added
#: without reopening the cutover's DBA ticket.
#:
#: ``processed`` is **unvalidated**: no handler exists yet, so nothing writes it.
#: It is listed because the UI must render it, not because it has been observed.
#:
#: ``unmapped`` is the odd one: it is not an action's lifecycle state at all, but a
#: request for a path this blueprint does not implement (see
#: :mod:`webapp.api.xras.unmapped`). It is deliberately **not** folded into
#: ``manual`` — that is the four-cause parking cohort operators filter on during
#: triage — nor into ``failed``, which would inflate the failure rate the dashboard
#: reports for something that never claimed to be supported.
XRAS_ACTION_STATUSES = ('received', 'processed', 'manual', 'failed', 'rechecked',
                        'unmapped')

#: Action types on the wire, for the filter dropdown. This is legacy's own declared
#: vocabulary (``action/domain/model/Action.java``: ``// New, Extension, Supplement,
#: Transfer, Renewal, Adjustment, Advance``), corrected against real payloads.
#:
#: WARNING: There is **no ``actionType`` of "Update"**. "Update" is a *handler*, not a
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
#:
#: WARNING: This is the **inbound** vocabulary and spells ``'Supplement'``. The
#: *outbound* one in ``queries/xras_access.py`` (``_SQL_ACTIONS``) spells the
#: same concept ``'Supplemental'``, because that CASE maps our own
#: ``allocation_transaction.transaction_type`` onto legacy's response bytes.
#: Neither is wrong and neither may be "fixed" to match the other — the
#: outbound spelling is pinned by the byte-clean parity gate. Noted in both
#: places because this codebase has already burned a sprint on a one-word
#: field-name mismatch.
XRAS_ACTION_TYPES = ('New', 'Renewal', 'Extension', 'Supplement',
                     'Transfer', 'Adjustment', 'Advance', 'Date Adjustment')

#: WARNING: ``Date Adjustment`` was added 2026-08-11 and is **not serviced**. It reached us
#: through the manual-fallback subject line of four forwarded payloads — the mechanism
#: ``XRAS_REIMPLEMENTATION.md`` § 1.4 identifies as the only record of the action types
#: SAM does not service — and it is absent from every other document because nothing
#: had ever seen it.
#:
#: It is listed here so the XRAS tab offers it as a filter chip **before** the first
#: row exists (``_xras_action_types`` unions this with observed values, so after that
#: it would appear anyway). Listing it changes no dispatch: ``select_service`` has no
#: arm for it, so it parks as ``manual`` — which is exactly what legacy does, there
#: being no ``DateAdjustProjectActionService``.
#:
#: Not serviced on purpose. Its payloads are Extension-shaped (dates, no resources),
#: so routing them to ``extend`` is a one-line change — and wrong twice over: an
#: Extension ignores ``actionBeginDate`` entirely (``date_adjustment_uwas0141`` asks
#: for one that differs from its allocation's), and rejects an end date earlier than
#: the current one, which is the likeliest reason a *separate* action type exists at
#: all. See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *What the corpus still does not cover*.

#: Wire spellings that mean the same handler, ``alias -> canonical``.
#:
#: XRAS sends ``actionType: "Adjustment"`` (measured — see
#: ``adjustment_uwis0064_manual.json``), but legacy's
#: ``AdjustProjectActionService.isServiceable`` tests ``equals("Adjust")``. The two
#: never match, so that handler has never once fired and every Adjustment falls
#: through ``ProjectActionServiceSelector`` to the manual-email fallback. Nothing has
#: shipped here yet, so SAM accepts **both** spellings rather than reproducing the
#: mismatch (see ``docs/xras/incoming/XRAS_REIMPLEMENTATION.md`` § 9, legacy defect 4).
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
#: WARNING: **DISPLAY ONLY. These must never decide whether a request number is a
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
#: dict's keys and the query re-validates below (defense in depth — a raw column
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


#: The two columns that can name a project, and the **only** place that pair is
#: spelled. ``request_number`` is the projcode for Extension/Supplement/Update;
#: ``projcode_result`` is the one SAM minted on the New path. Either may match and
#: nothing in the row distinguishes them — the two are the same shape.
_PROJCODE_COLUMNS = (XrasActionLog.projcode_result, XrasActionLog.request_number)

#: Newest action first. WARNING: **The id is not decoration.** ``received_time`` is a MySQL
#: ``DATETIME`` — one-second resolution — and XRAS posts arrive in bursts, so ties
#: are ordinary. Every consumer of the projcode join orders by this so they cannot
#: name different rows for the same project.
_LATEST_ACTION_ORDER = (XrasActionLog.received_time.desc(),
                        XrasActionLog.xras_action_log_id.desc())


def action_names_project(projcode):
    """The ``projcode_result`` OR ``request_number`` match, as one clause.

    WARNING: **This is the subtlest logic in the feature and it is spelled here
    only.** Do not re-derive it per call site. Three separate spellings disagree
    on how to break a same-second tie: ordering by ``(received_time, id)`` is
    deterministic, while merging two per-column queries that compare only
    ``received_time`` lets whichever column is iterated first win. The
    card could then show one action as the reason a project was pending while
    ``xras_activation_event`` stamped a different one as the provenance of what the
    operator did about it.

    *projcode* may be a literal or a column expression, so the same clause serves the
    single-project lookup and the join.
    """
    return or_(*(column == projcode for column in _PROJCODE_COLUMNS))


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
    source_action,
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
        _in(XrasActionLog.source_action_id, source_action),
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
        query = query.filter(XrasActionLog.source_action_id.isnot(None))
    elif replays_only is False:
        query = query.filter(XrasActionLog.source_action_id.is_(None))

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
    source_action: Optional[Union[int, List[int]]] = None,
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
        - ``source_action_id``, ``recheck_count`` (how many replays this row has spawned)
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
    # table renders a "rechecked" chip per row, and N+1 on a 200-row page is the
    # kind of thing that only shows up once production has volume.
    recheck_child = XrasActionLog.__table__.alias('recheck_child')
    recheck_count = (
        session.query(func.count(recheck_child.c.xras_action_log_id))
        .filter(recheck_child.c.source_action_id == XrasActionLog.xras_action_log_id)
        .correlate(XrasActionLog)
        .scalar_subquery()
    )

    query = _apply_action_filters(
        session.query(XrasActionLog, recheck_count.label('recheck_count')),
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
        source_action=source_action,
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
            'action_id': row.action_id,
            'status': row.status,
            'service': row.service,
            'outcome_reason': row.outcome_reason,
            'http_status': row.http_status,
            'errors': _split_errors(row.error_messages),
            'projcode_result': row.projcode_result,
            'processed_time': row.processed_time,
            'processed_by': row.processed_by,
            'source_action_id': row.source_action_id,
            'recheck_count': n_replays or 0,
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

    # The same column pair as :data:`_PROJCODE_COLUMNS`, read off the already-built
    # dicts rather than the ORM — this is the one consumer that has the values in
    # hand and wants a set-membership answer per row, not a join. Sourced from the
    # tuple so adding a third projcode-bearing column reaches all three consumers.
    codes = {c for item in rows
             for c in (item[column.key] for column in _PROJCODE_COLUMNS) if c}
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
    source_action: Optional[Union[int, List[int]]] = None,
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
        source_action=source_action,
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
        source_action=None,
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


def get_observed_action_types(session: Session) -> List[str]:
    """Every ``action_type`` present in the log, folded onto canonical spellings.

    Alias pairs collapse to one entry — ``Adjust`` and ``Adjustment`` are the same
    action and filtering on either returns both, so offering two chips that filter
    identically would read as two distinct action types.
    """
    return sorted({
        canonical_action_type(row.action_type)
        for row in session.query(XrasActionLog.action_type)
        .filter(XrasActionLog.action_type.isnot(None))
        .distinct().all()
    })


def get_projects_by_ids(session: Session, project_ids) -> List[Project]:
    """The ``Project`` rows for *project_ids*, or an empty list for an empty input.

    An empty ``IN ()`` is legal SQL but a pointless round trip, and the guard is the
    kind of thing that gets forgotten at the second call site.
    """
    if not project_ids:
        return []
    return (session.query(Project)
            .filter(Project.project_id.in_(project_ids)).all())




def audit_resource_mapping(session: Session, *,
                           xras_keys: Optional[Iterable[int]] = None
                           ) -> Dict[str, Any]:
    """Which SAM resources XRAS can and cannot name, in four groups.

    ``xras_resource_repository_key_resource`` maps an XRAS ``resourceRepositoryKey``
    to a SAM resource, and it is the join behind two different things:

    * on the **write** side, an unmapped key is
      ``No resource found in SAM corresponding to key %s`` — the action fails.
    * on the **read** side, ``resourceRepositoryKey`` is simply *omitted* from the
      GET payloads when a resource has no row, so **closing a gap moves response
      bytes**. That is why this is a pre-cutover check and not a post-cutover one:
      adding a mapping after the parity run invalidates it.

    Three groups because they need different actions: active resources with no
    mapping (the ones that break awards), mapping rows pointing at decommissioned
    kit (harmless but misleading), and rows whose resource has vanished entirely (a
    broken FK, which should be impossible).

    WARNING: **An unmapped active resource is not a failure.** Not every internal resource
    is offered for allocation through XRAS, so most of the unmapped ones are unmapped
    by design — stably 11 of them across snapshot refreshes. Only ``dangling`` is
    unambiguously broken. The caller decides what to do with that; this reports.

    Lives here rather than in ``cli/xras/builders.py``, where it was: builders are
    ORM->dict extractors per ``src/cli/README.md``, and a webapp surface that wants
    the same audit should not have to import the CLI to get it.

    **The fourth group needs *xras_keys*.** Read from two local tables alone, this
    function has no list of the keys XRAS will actually send — so the failure that
    genuinely breaks an award, XRAS naming a ``resourceRepositoryKey`` SAM has no
    row for, was invisible here and surfaced only at runtime as
    ``No resource found in SAM corresponding to key %s``. Pass the live catalog
    (``sam.integration.xras_api.resource_repository_keys()``) and it becomes
    ``xras_only_keys``.

    The iterable is **injected rather than fetched**, so this function keeps zero
    network knowledge and stays usable offline: ``xras_keys=None`` reproduces the
    previous report byte for byte, with ``live_checked`` False to say so.
    """
    from sam.integration.xras import XrasResourceRepositoryKeyResource
    from sam.resources.resources import Resource

    rows = session.query(XrasResourceRepositoryKeyResource).all()

    # WARNING: Keyed by resource_id, so two keys pointing at ONE resource collapse to the
    # last one seen while ``mapped`` below still counts both. Left as-is because the
    # column is the mapping's primary key and the duplicate is itself the anomaly —
    # but it is why these two numbers can disagree.
    by_resource_id = {r.resource_id: r for r in rows}

    unmapped_active, mapped_decommissioned, dangling = [], [], []

    for resource in session.query(Resource).all():
        row = by_resource_id.get(resource.resource_id)
        commissioned = resource.is_commissioned_at()
        if row is None:
            if commissioned:
                unmapped_active.append(resource.resource_name)
        elif not commissioned:
            mapped_decommissioned.append(
                {'key': row.resource_repository_key,
                 'resource': resource.resource_name})

    for row in rows:
        if row.resource is None:
            dangling.append(row.resource_repository_key)

    known_keys = {row.resource_repository_key for row in rows}
    xras_only = (sorted(int(k) for k in xras_keys if int(k) not in known_keys)
                 if xras_keys is not None else [])

    return {
        'mapped': len(rows),
        'unmapped_active': sorted(unmapped_active),
        'mapped_decommissioned': sorted(mapped_decommissioned,
                                        key=lambda d: d['resource']),
        'dangling_keys': sorted(dangling),
        # Keys XRAS sends that SAM cannot resolve — the one that breaks awards.
        'xras_only_keys': xras_only,
        # Distinguishes "XRAS sends nothing SAM lacks" from "we never asked".
        'live_checked': xras_keys is not None,
        'live_key_count': len(set(xras_keys)) if xras_keys is not None else None,
    }


def audit_opportunity_mapping(session: Session, *,
                              opportunity_ids: Optional[Iterable[int]] = None
                              ) -> Dict[str, Any]:
    """Which XRAS opportunities SAM can and cannot resolve to an allocation type.

    The ``opportunityId`` analogue of :func:`audit_resource_mapping`, and it copies
    that function's contract deliberately, including the wart-free half: the ids are
    **injected rather than fetched**, so this keeps zero network knowledge and stays
    usable offline. ``opportunity_ids=None`` reports the local half only, with
    ``live_checked`` False to say so.

    WARNING: **An unmapped opportunity is not a failure.** With an empty table *every*
    opportunity is unmapped and ingestion is completely healthy — the ladder resolves
    it, exactly as it did before the map existed. This is a diagnostic, not a gate.
    The one genuinely broken state is ``dangling``: a mapping row whose
    ``allocation_type`` has vanished, or has no panel, which the ingest-side lookup
    must treat as a miss and which no operator would otherwise see.

    Callers with ids in hand should pass them: ``xras_sweep`` already holds an
    ``opportunityId`` on every ``reports/requests`` payload it enumerates, so its
    half of this costs no extra round trips.
    """
    from sam.integration.xras import XrasOpportunityAllocationType

    rows = session.query(XrasOpportunityAllocationType).all()

    dangling = [row.opportunity_id for row in rows
                if row.allocation_type is None or row.allocation_type.panel is None]

    known_ids = {row.opportunity_id for row in rows}
    seen = {int(i) for i in opportunity_ids} if opportunity_ids is not None else None
    unmapped = sorted(i for i in seen if i not in known_ids) if seen else []

    return {
        'mapped': len(rows),
        'mapped_ids': sorted(known_ids),
        # Rows the ingest-side lookup would silently fall through on.
        'dangling_ids': sorted(dangling),
        # Ids seen in the wild with no map row — these fall back to the ladder.
        'unmapped_ids': unmapped,
        # Distinguishes "nothing unmapped out there" from "we never asked".
        'live_checked': opportunity_ids is not None,
        'live_id_count': len(seen) if seen is not None else None,
    }


def propose_opportunity_mapping(session, opportunity_payloads) -> Dict[str, Any]:
    """Which unmapped opportunities can be mapped automatically, and which cannot.

    Two **independent** derivations must agree before an opportunity is
    proposed:

    1. ``sam.xras.opportunity_types`` — XRAS's own ``allocationTypeId`` plus its
       primary ``panelId``, through an eight-entry constant.
    2. The free-text ladder in ``sam.xras.extractors``, run on the opportunity's
       name and type.

    Agreement is the whole safety rule, and it is not belt-and-braces. Measured
    across all 27 opportunities in the NCAR process on 2026-08-20, the two
    disagree **twice**, and both times XRAS is the one that is wrong about SAM:
    ``University small request - unsponsored`` (XRAS calls it ``Educational``;
    SAM means ``Small (No NSF award)``) and ``NCAR - ASD Opportunity`` (XRAS
    gives it NSC's type *and* panel; SAM means ``ASD-NCAR``, a different
    **facility**, which is what reaches ``next_projcode``). Requiring agreement
    withholds both without needing to know about either — and withholds the
    first Wyoming opportunity too, which is exactly the one a human should see.

    It also makes a mistake in the constant self-limiting: a wrong entry
    disagrees with the ladder and is withheld rather than written.

    WARNING: **The ladder half is an approximation, deliberately on the conservative
    side.** At ingest the chain also sees ``requestTitle``, which an opportunity
    payload has no equivalent of, so two strategies (``_csl_strategy`` and part
    of ``_external_strategy``) cannot fire here. That can only turn an agreement
    into a non-agreement — never the reverse — so the failure mode is "asks a
    human unnecessarily", not "writes something wrong".

    Reads the database and nothing else: **no network, no writes.** Payloads are
    injected exactly as ``audit_resource_mapping`` injects ``xras_keys``, so the
    sweep, the CLI and the tests all share one decision rather than three
    reimplementations of it.

    Returns ``{'agree': [...], 'review': [...], 'unknown_pair': [...]}``, each
    entry carrying ``opportunity_id``/``opportunity_name`` plus whichever pairs
    were derived — ``review`` carries **both**, so a ledger row explains itself
    without a second query.
    """
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Panel
    from sam.xras.extractors import select_allocation_type_parms
    from sam.xras.opportunity_types import pair_for_opportunity

    agree: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []

    for payload in opportunity_payloads or []:
        if not isinstance(payload, dict):
            continue
        opportunity_id = payload.get('opportunityId')
        if opportunity_id is None:
            continue
        name = payload.get('opportunityName')
        entry = {'opportunity_id': int(opportunity_id), 'opportunity_name': name}

        mapped = pair_for_opportunity(payload)
        parms = select_allocation_type_parms({
            'opportunityName': name,
            'allocationType': payload.get('allocationType'),
            'requestTitle': None,
        })
        ladder = (parms.panel, parms.allocation_type) if parms else None

        if mapped is None:
            # A genuinely new allocation product looks exactly like this. It is
            # reported, never guessed at — adding it is a one-line edit to the
            # constant, which is a code review rather than a silent DB write.
            unknown.append({**entry, 'ladder': ladder})
            continue

        if ladder != mapped:
            review.append({**entry, 'ladder': ladder, 'xras': mapped})
            continue

        panel_name, type_name = mapped
        row = (session.query(AllocationType)
               .join(Panel, AllocationType.panel_id == Panel.panel_id)
               .filter(Panel.panel_name == panel_name)
               .filter(AllocationType.allocation_type == type_name)
               .first())
        if row is None:
            # The constant names a pair the lookup tables do not have. A unit
            # test pins against this, so reaching it means the tables moved
            # under us — report rather than write a dangling row.
            review.append({**entry, 'ladder': ladder, 'xras': mapped,
                           'missing_allocation_type': True})
            continue

        agree.append({**entry, 'pair': mapped,
                      'allocation_type_id': row.allocation_type_id})

    key = lambda d: d['opportunity_id']          # noqa: E731 - sort key only
    return {
        'agree': sorted(agree, key=key),
        'review': sorted(review, key=key),
        'unknown_pair': sorted(unknown, key=key),
    }
