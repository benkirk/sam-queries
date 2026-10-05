"""XRAS action-log page and its worklist card fragments.

The read surface of the Allocations -> XRAS tab: the ``/xras`` page shell plus
the four HTMX fragment routes it loads (action log, activity/pending, the
Pending Users worklist, and the shared window control). Moved out of
``blueprint.py``; the constants and filter/facet helpers live in ``_shared``.
"""

from datetime import datetime, timedelta

from flask import render_template, request, url_for
from flask_login import current_user, login_required

from webapp.extensions import db
from webapp.utils.project_permissions import can_edit_project_governance
from webapp.utils.rbac import (
    Permission, has_permission, has_permission_any_facility, require_permission,
)
from sam.queries.xras_actions import (
    XRAS_ACTION_SORT_COLUMNS,
    XRAS_ACTION_STATUSES,
    XRAS_ACTION_TYPES,
    XRAS_REQUEST_TOKEN_EXAMPLE,
    count_recent_xras_actions,
    get_observed_action_types,
    get_projects_by_ids,
    get_recent_xras_actions,
    summarize_xras_actions,
)
from sam.queries.xras_activation import (
    ATTENTION_RECENT_DAYS,
    needs_attention,
    notify_only_project_ids,
    get_xras_activity,
    get_xras_pending_recipients,
)
from sam.queries.xras_accounts import (
    enrich_worklist,
    get_account_worklist,
    load_pending_worklist_rows,
    stamp_merge_targets,
    stamp_project_existence,
    stamp_waiting_days,
    worklist_counts,
)

from .. import bp
from ..blueprint import _window_control_context
from ._shared import (
    _activity_in_window,
    scope_rows,
    ACCOUNT_FACETS, ACTIVITY_FACETS, REMEDIATION_FACETS,
    _ACCOUNTS_ENRICH_BUDGET, _ACCOUNTS_FORM_ID, _ACCOUNTS_TARGET,
    _ACTIVITY_WINDOW_PILLS, _XRAS_ACTIVITY_FORM_ID,
    _XRAS_ACTIVITY_TARGET, _XRAS_FORM_ID, _XRAS_FRAGMENT_TARGET,
    _parse_activity_window, _parse_xras_filters,
    _submitted_since,
)
from webapp.utils.faceted_log import build_facet_strip
from webapp.utils.htmx import read_flag, read_sort, read_tab, sort_rows


#: Pending Users' sortable non-facet columns -> row key. Needs / Role / Source /
#: Request / Identity are the chips' job.
def _person_sort_key(row):
    person = row.get('person') or {}
    name = ((person.get('firstName') or '') + ' '
            + (person.get('lastName') or '')).strip()
    return (name or row.get('username') or '').casefold()


_ACCOUNTS_SORT = {
    'username': lambda r: (r.get('username') or '').casefold(),
    'person': _person_sort_key,
    'waiting': lambda r: r.get('waiting_days'),
}


# XRAS action log -- the operator surface for POST /api/xras/v1/actions.
#
# Two permissions: VIEW_XRAS covers the page, table, filters and error lists
# (swept into ALL_VIEW by name); MANAGE_XRAS covers the raw-payload panel and
# the replay button, the payload being the request body verbatim with
# participant names, emails, phones and grant-officer contacts.
#
# Plain require_permission(), NOT require_permission_any_facility(): an XRAS
# action is not facility-scopable -- it arrives before we know its facility, and
# a malformed body has none at all. See rbac.py's USER_FACILITY_PERMISSIONS.


def _xras_action_types():
    """Filter vocabulary: the known types plus anything actually in the table.

    ``XrasActionSchema`` applies no enum to ``actionType`` on purpose — Transfer,
    Renewal and Advance still have zero samples and no co-PI role has ever been
    sampled — so a type we have never seen must still be filterable rather than
    invisible. Union, don't replace.

    Observed values are folded onto their canonical spelling first, so an alias pair
    offers **one** entry: ``Adjust`` and ``Adjustment`` are the same action and
    filtering on either returns both (``XRAS_ACTION_TYPE_ALIASES``). Two chips that
    filter identically would read as two distinct action types.
    """
    return sorted(set(XRAS_ACTION_TYPES) | set(get_observed_action_types(db.session)))


#: Deep-link targets (?view=) for the XRAS page. The two tab panes plus the two
#: sibling cards (remediations/logs) — not a uniform tablist, so the two cards
#: are reached by a scroll-into-view handler (data-deeplink), not the tab
#: channel. First entry is the default.
_XRAS_VIEWS = ('activity', 'accounts', 'remediations', 'logs')


@bp.route('/xras')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras():
    """XRAS action-log page: the operator surface for the ingest endpoint."""
    end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=30)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    return render_template(
        'dashboards/allocations/xras.html',
        xras_start_date=start_str,
        xras_end_date=end_str,
        # Which worklist tab / card a ?view= deep-link opened on (see xras.html).
        xras_view=read_tab('view', _XRAS_VIEWS, 'activity'),
        # The worklist tabs share ONE window control, rendered in the shell so
        # only one start_date/end_date pair exists (see the template).
        window=_parse_activity_window(request.args),
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        form_id='xras-window-filters',
        **_window_control_context(end_date, start_str, end_str),
        all_statuses=list(XRAS_ACTION_STATUSES),
        all_action_types=_xras_action_types(),
        activity_facets=ACTIVITY_FACETS,
        account_facets=ACCOUNT_FACETS,
        remediation_facets=REMEDIATION_FACETS,
        # Site-specific, so it lives with the token family rather than in the
        # template — see XRAS_REQUEST_TOKEN_PREFIXES.
        request_example=XRAS_REQUEST_TOKEN_EXAMPLE,
    )


@bp.route('/xras_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_fragment():
    """HTMX fragment: sortable, paginated table of XRAS actions."""
    filters, sort, page = _parse_xras_filters(request.args)
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_xras_actions(
        db.session,
        **filters,
        sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
        offset=offset, limit=page['per_page'],
    )
    total = count_recent_xras_actions(db.session, **filters)
    # Facet counts with SELF-EXCLUSION: each dimension's rollup omits its OWN
    # filter while honoring every other one. That is what makes the chips
    # switchers rather than dead ends -- scope a dimension by itself and picking
    # "failed" drives the other four statuses to 0, with no way to move between
    # them without clearing the filter first. Costs two GROUP BY queries instead
    # of one, both served by the (status, action_type) triage index.
    _facet_common = dict(
        request_number=filters['request_number'],
        start_date=filters['start_date'], end_date=filters['end_date'],
    )
    status_facet = summarize_xras_actions(
        db.session, action_type=filters['action_type'], **_facet_common)
    type_facet = summarize_xras_actions(
        db.session, status=filters['status'], **_facet_common)

    # Every declared status renders, including at zero, and a status outside
    # the vocabulary (only ever a bad write) appends after them, so the strip
    # agrees with the headline total. A stray chip still filters although
    # `all_statuses` offers only the declared ones: `set-filter-submit` adds a
    # missing <option>. That offer list is deliberately not widened.
    status_facets = build_facet_strip(status_facet['by_status'],
                                      XRAS_ACTION_STATUSES)
    # A NULL action_type is a real count (an unparseable body has none) but
    # not a filterable value, so the observed-only strip drops it.
    action_type_facets = build_facet_strip(type_facet['by_action_type'])

    # `configured` gates the Request # -> detail-modal link: the modal needs a
    # live outbound read, so a site with XRAS incoming-only degrades to the
    # plain/project cell (see xras_table.html). The Result column is unaffected.
    from sam.integration.xras_api import xras_api_configured

    return render_template(
        'dashboards/allocations/partials/xras_table.html',
        rows=rows, total=total,
        status_facets=status_facets, action_type_facets=action_type_facets,
        page=page, sort=sort, filters=filters,
        fragment_url=url_for('allocations_dashboard.xras_fragment'),
        target_id=_XRAS_FRAGMENT_TARGET,
        form_id=_XRAS_FORM_ID,
        sortable_columns=sorted(XRAS_ACTION_SORT_COLUMNS),
        configured=xras_api_configured(),
        can_view_projects=has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
    )


@bp.route('/xras_pending_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_pending_fragment():
    """HTMX fragment: recent XRAS outcomes — what was communicated, what needs a human.

    One row per successfully processed action. See :func:`get_xras_activity` for
    why the key is the action rather than the project, and for what this can and
    cannot see.

    The endpoint keeps its old name. It is an internal URL, ~30 tests pin it, and
    renaming it would buy nothing over changing what it renders.

    Two gates, both enforced HERE rather than only in the template:

    - ``recipients`` (project lead/admin contact details) and the per-recipient
      delivery detail are assembled only for ``MANAGE_XRAS``, so a ``VIEW_XRAS``
      response never carries the addresses at all and a view-source cannot leak
      what the page chose not to draw. Same rule as the raw-payload panel.
    - ``may_activate`` is resolved **per project** through
      ``can_edit_project_governance``, not once for the card. The helper is flat
      over the user today, so this costs one extra query and buys nothing
      immediately — but the moment it becomes project- or facility-aware the card
      follows for free, whereas a card-level flag would quietly start lying. The
      POST route calls the same helper itself, so the authority stays in one
      place and this is only ever a rendering hint.
    """
    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    show_all = read_flag(request.args, 'show_all')
    window = _parse_activity_window(request.args)
    selected = ACTIVITY_FACETS.read(request.args)

    # All time, then scoped in Python: the queue has no date bound (a New
    # nobody activated months ago is its point) and the badges need both
    # counts. `show_all` applies the window with the same inclusive bounds.
    everything = get_xras_activity(db.session)
    now = datetime.now()

    def queue(row):
        return needs_attention(row, now=now)

    in_window = [r for r in everything if _activity_in_window(r, window)]
    attention_total = sum(1 for r in everything if queue(r))
    queued_in_window = sum(1 for r in in_window if queue(r))
    rows = scope_rows(everything, request.args,
                      queue=queue, in_window=_activity_in_window)

    facet_values = ACTIVITY_FACETS.strips(rows, selected)
    rows = ACTIVITY_FACETS.apply(rows, selected)

    recipients = {}
    may_activate = {}
    if may_manage:
        project_ids = sorted({r['project_id'] for r in rows})
        recipients = get_xras_pending_recipients(db.session, project_ids)
        may_activate = {
            p.project_id: can_edit_project_governance(current_user, p)
            for p in get_projects_by_ids(db.session, project_ids)
        }

    return render_template(
        'dashboards/allocations/partials/xras_activity_card.html',
        rows=rows,
        recipients=recipients,
        may_activate=may_activate,
        may_manage=may_manage,
        window=window,
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        facet_values=facet_values,
        selected=selected,
        show_all=show_all,
        attention_total=attention_total,
        window_total=len(in_window),
        hidden_count=len(in_window) - queued_in_window,
        outside_count=attention_total - queued_in_window,
        recent_days=ATTENTION_RECENT_DAYS,
        # Bulk "dismiss notify-only" is an ADMIN_XRAS lever: the count is over
        # the whole set (not the window), matching "skip ALL pending notices".
        notify_only_count=len(notify_only_project_ids(everything)),
        can_bulk_dismiss=has_permission(current_user, Permission.ADMIN_XRAS),
        can_view_projects=has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
        form_id=_XRAS_ACTIVITY_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_pending_fragment'),
        target_id=_XRAS_ACTIVITY_TARGET,
    )


# Account-creation worklist -- who must exist in SAM before an XRAS handoff can
# succeed. Unreconciled ARC placeholder identities are 55% of production XRAS
# failures and account creation is manual, so this is the operator's queue for
# the largest single cause of failure.
#
# Read-only by design: operator notes and dismissal need storage
# `XrasActivationEvent` cannot provide, its `project_id` being NOT NULL and
# project-scoped while this worklist is username-keyed and a New request has no
# project yet. `xras_account_event` is the follow-up; buttons before it would be
# dead UI.


@bp.route('/xras_accounts_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_accounts_fragment():
    """HTMX fragment: the Pending Users worklist — everyone who needs a SAM
    account before an XRAS handoff can land, from BOTH feeds.

    Feed A (posted ``xras_action_log`` rows) is always available; Feed B (the
    sweep's lookahead at approved, not-yet-pushed requests) is contingent on the
    outbound API and a published sweep. They are unioned on the casefolded
    username, with a per-row source badge and received-push rows pinned first.
    When Feed B is unavailable the card shows the received-push half and a
    degraded-half note says which state it is in — never a blank tab.

    PII is gated **here**, not in the template, and **after** the merge: person
    detail is assembled only for a viewer holding MANAGE_XRAS, so a VIEW_XRAS
    response never carries it. `is_reconciled` is deliberately on the VIEW_XRAS
    side of that line — a boolean about account state, not a personal detail,
    and the signal that an item is about to close itself.
    """
    from sam.integration.xras_api import xras_api_configured

    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    window = _parse_activity_window(request.args)
    selected = ACCOUNT_FACETS.read(request.args)

    # Feed B, filtered BEFORE injection: after the merge every Feed-A row would
    # pass `_submitted_since` (no submit_date => kept), so the window has to bite
    # here or it would never narrow the pending half. `pending_hidden` is the
    # Feed-B-scoped "outside the date filter" count -- denominator is the
    # snapshot's rows, not its counts total.
    feed = load_pending_worklist_rows()
    pending = [r for r in feed.rows if _submitted_since(r, window['since'])]
    pending_hidden = len(feed.rows) - len(pending)

    rows = get_account_worklist(db.session, since=window['since'],
                                until=window['until'], pending_rows=pending)

    stamp_waiting_days(rows)
    # Safe after the merge copies each action dict: this writes `is_project` in
    # place, and Feed-B actions gain it too so `request_cell` can link them.
    stamp_project_existence(db.session, rows)

    # Enrichment is best-effort and never fatal: it skips rows Feed B already
    # carried a person for, and an outage leaves the rest `person=None`.
    enrichment = enrich_worklist(rows, max_lookups=_ACCOUNTS_ENRICH_BUDGET)
    # After enrichment (a Feed-A row has no email until then) and before the
    # PII strip: the matched USERNAME is account state and rides top-level.
    stamp_merge_targets(db.session, rows)
    # The account_request the sweep wrote for this username, if any: the
    # claim/dismiss state the card can now act on. Account state, not PII, so
    # it rides on the VIEW_XRAS side; the controls themselves are gated on
    # MANAGE_ACCOUNT_REQUESTS in the template.
    from sam.queries.account_requests import stamp_account_requests
    stamp_account_requests(db.session, rows)

    if not may_manage:
        # After the merge every row is a copy, so strip in place -- and only
        # here, never on `feed.rows`/`pending`, which are still the cached
        # snapshot's own dicts. `is_reconciled` survives -- see the docstring.
        for row in rows:
            row['person'] = None

    facet_values = ACCOUNT_FACETS.strips(rows, selected)
    rows = ACCOUNT_FACETS.apply(rows, selected)

    # No forced default: with no header clicked the source order stands
    # (received-push rows pinned first), and sort_rows is a no-op.
    sort = read_sort(request.args, set(_ACCOUNTS_SORT), default_dir='desc')
    rows = sort_rows(rows, sort, _ACCOUNTS_SORT)

    snapshot = feed.snapshot or {}
    return render_template(
        'dashboards/allocations/partials/xras_accounts_card.html',
        rows=rows,
        counts=worklist_counts(rows),
        sort=sort,
        sortable_columns=set(_ACCOUNTS_SORT),
        may_manage=may_manage,
        enrichment=enrichment,
        window=window,
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        facet_values=facet_values,
        selected=selected,
        # The Feed-B half's state, so the card can render a degraded-half note
        # and its freshness line instead of pretending it saw everything.
        feed_checked=feed.checked,
        feed_reason=feed.reason,
        feed_generated_at=snapshot.get('generated_at'),
        feed_window_days=snapshot.get('window_days'),
        pending_hidden=pending_hidden,
        form_id=_ACCOUNTS_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_accounts_fragment'),
        target_id=_ACCOUNTS_TARGET,
        # Gates the Request -> detail-modal link (needs a live outbound read);
        # incoming-only degrades to today's project/plain cell. See request_cell.
        configured=xras_api_configured(),
    )


@bp.route('/xras_window_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_window_fragment():
    """HTMX fragment: just the shared window pills.

    WARNING: **The control has to re-render itself, and this is why the route
    exists.** `window_pills` marks the active pill server-side from the
    window it is handed. While the pills lived inside each worklist fragment
    that came free — a submit re-rendered the fragment and the pill state
    came with it. Sharing one control across three tabs moved it into the page
    shell, outside every swap target, so it kept whatever state the page load
    gave it: clicking 7D re-filtered the data and left the pill looking
    unchanged, which reads as a dead control.

    So the pills are their own swap target, listening for the same submit the
    panes do.
    """
    window = _parse_activity_window(request.args)
    return render_template(
        'dashboards/allocations/partials/xras_window_control.html',
        window=window, window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        form_id='xras-window-filters')

