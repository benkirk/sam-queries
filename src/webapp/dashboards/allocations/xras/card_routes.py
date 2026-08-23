"""XRAS action-log page and its worklist card fragments.

The read surface of the Allocations → XRAS tab: the ``/xras`` page shell plus
the five HTMX fragment routes it loads (action log, activity/pending, accounts,
the shared window control, and pending requests). Moved verbatim out of
``blueprint.py``; the constants and filter/facet helpers live in ``_shared``.
"""

from datetime import datetime, timedelta

from flask import render_template, request, url_for
from flask_login import current_user, login_required

from webapp.extensions import db
from webapp.utils.project_permissions import can_edit_project_governance
from webapp.utils.rbac import Permission, has_permission, require_permission
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
    ACTIVITY_TAGS,
    get_xras_activity,
    get_xras_pending_recipients,
)
from sam.queries.xras_accounts import (
    CLASSIFICATION_ABSENT,
    CLASSIFICATION_INACTIVE,
    enrich_worklist,
    get_account_worklist,
    stamp_project_existence,
    stamp_waiting_days,
    worklist_counts,
)

from .. import bp
from ..blueprint import _window_control_context
from ._shared import (
    ORIGIN_KNOWN, ORIGIN_PLACEHOLDER, _ACCOUNT_CLASSIFICATION_LABELS,
    _ACCOUNTS_ENRICH_BUDGET, _ACCOUNTS_FORM_ID, _ACCOUNTS_TARGET,
    _ACTIVITY_TAG_LABELS, _ACTIVITY_WINDOW_PILLS, _ORIGIN_LABELS,
    _PENDING_FORM_ID, _PENDING_TARGET, _XRAS_ACTIVITY_FORM_ID,
    _XRAS_ACTIVITY_TARGET, _XRAS_FORM_ID, _XRAS_FRAGMENT_TARGET,
    _account_facets, _activity_facets, _filter_accounts, _filter_activity,
    _parse_activity_window, _parse_xras_filters, _pending_account_total,
    _request_facets, _submitted_since,
)


# ============================================================================
# XRAS action log — the operator surface for POST /api/xras/v1/actions
#
# Gating, and why it is two permissions:
#   VIEW_XRAS    the page, the table, the filters, the error lists. Swept into
#                ALL_VIEW by name, so every operator bundle already has it.
#   MANAGE_XRAS  the raw-payload panel and the replay button. The payload is the
#                request body verbatim and carries participant names, emails,
#                phones and grant-officer contacts.
#
# Plain require_permission(), NOT require_permission_any_facility(): an XRAS
# action is not facility-scopable. It arrives before we know its facility (a New
# action has no project yet) and a malformed body has none at all — there is
# nothing to intersect a scope against. See the note in rbac.py's
# USER_FACILITY_PERMISSIONS.
# ============================================================================


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
        # The worklist tabs share ONE window control, rendered in the shell so
        # only one start_date/end_date pair exists (see the template).
        window=_parse_activity_window(request.args),
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        form_id='xras-window-filters',
        **_window_control_context(end_date, start_str, end_str),
        all_statuses=list(XRAS_ACTION_STATUSES),
        all_action_types=_xras_action_types(),
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
    # Facet counts, computed with SELF-EXCLUSION: each dimension's rollup omits
    # its OWN filter while honoring every other one.
    #
    # This is what makes the chips switchers rather than dead ends. Scoping a
    # dimension by itself drives every unselected value to zero the moment one is
    # picked — click "failed" and the other four statuses all read 0, so there is
    # no way to move to another status without first clearing the filter. The
    # jobs explorer's facet strip learned the same lesson (service.jobs_facets
    # passes self_exclude).
    #
    # Two GROUP BY queries instead of one; both are served by the
    # (status, action_type) triage index.
    _facet_common = dict(
        request_number=filters['request_number'],
        start_date=filters['start_date'], end_date=filters['end_date'],
    )
    status_facet = summarize_xras_actions(
        db.session, action_type=filters['action_type'], **_facet_common)
    type_facet = summarize_xras_actions(
        db.session, status=filters['status'], **_facet_common)

    # Every status renders, including at zero — an absent bucket would read as
    # "not measured" rather than "none". `summarize_xras_actions` already seeds
    # the five, so iterating its dict gives that for free, in vocabulary order.
    #
    # ⚠️ Iterated, not re-derived from XRAS_ACTION_STATUSES. That spelling dropped
    # any status outside the vocabulary — which the query layer goes out of its way
    # to keep, because it is a bug worth surfacing — while the headline total above
    # still counted it, so the strip disagreed with its own total.
    #
    # A stray appends rather than reshuffling: the five are a stable strip an
    # operator scans by position.
    #
    # Its chip filters even though `all_statuses` (line ~1295) still offers only the
    # five: `set-filter-submit` synthesizes a missing <option> before setting the
    # value (static/js/actions.js:152-160). The offer list is deliberately NOT
    # widened the way `_xras_action_types` widens its own — an unsampled action type
    # is normal traffic, a stray status is only ever a bad write, and presenting one
    # as a standing filter choice would dress a bug up as a category.
    status_facets = [{'value': s, 'count': n}
                     for s, n in status_facet['by_status'].items()]

    # A NULL action_type is a real count — a body that would not parse has none —
    # but it is not a filterable value: there is no way to express "IS NULL"
    # through the form's multi-select. Dropped rather than rendered as a chip
    # that cannot work, the same rule the jobs facet strip applies.
    action_type_facets = sorted(
        ({'value': t, 'count': n}
         for t, n in type_facet['by_action_type'].items() if t),
        key=lambda r: (-r['count'], r['value']),
    )

    # `configured` gates the Request # → detail-modal link: the modal needs a
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
    window = _parse_activity_window(request.args)
    selected_tags = [t for t in request.args.getlist('tag') if t]
    selected_types = [t for t in request.args.getlist('activity_type') if t]

    rows = get_xras_activity(db.session,
                             since=window['since'], until=window['until'])

    # Facets are computed over the *unfiltered* window set, each dimension
    # dropping its own selection — the same self-exclusion `facet_notifications`
    # and `xras_fragment` keep. Scope a dimension by itself and every unselected
    # value falls to zero the moment one is picked, and the chips stop being
    # switchers.
    tag_facets = _activity_facets(rows, 'tag', types=selected_types)
    type_facets = _activity_facets(rows, 'activity_type', tags=selected_tags)

    rows = _filter_activity(rows, tags=selected_tags, types=selected_types)

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
        # Every declared tag renders, including at zero: an absent chip reads
        # as "not measured", which is a different claim from "none".
        tag_values=[{'value': tag,
                     'label': _ACTIVITY_TAG_LABELS.get(tag, tag),
                     'count': tag_facets.get(tag, 0)}
                    for tag in ACTIVITY_TAGS],
        type_values=[{'value': k, 'count': v} for k, v in type_facets.items()],
        selected_tags=selected_tags,
        selected_types=selected_types,
        form_id=_XRAS_ACTIVITY_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_pending_fragment'),
        target_id=_XRAS_ACTIVITY_TARGET,
    )


# ---------------------------------------------------------------------------
# Account-creation worklist — read-only.
#
# Who must exist in SAM before an XRAS handoff can succeed. Unreconciled ARC
# placeholder identities are 55% of production XRAS failures, and account
# creation is manual, so this card is the operator's queue for the largest
# single cause of failure.
#
# Read-only in this PR by design. Operator notes and dismissal need storage
# that `XrasActivationEvent` cannot provide — its `project_id` is NOT NULL and
# project-scoped, while this worklist is username-keyed and for a New request
# the project does not exist yet. That table (`xras_account_event`) is the
# immediate follow-up; shipping the buttons before it would be dead UI.
# ---------------------------------------------------------------------------


@bp.route('/xras_accounts_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_accounts_fragment():
    """HTMX fragment: accounts that must be created or reactivated for XRAS.

    Two states here are **designed, not broken**, and both are what a reviewer
    will see first:

    - **Unconfigured.** With `XRAS_OUTGOING_ENABLED` off — the shipped state,
      and what staging shows — the worklist still renders in full from the
      inbound action log. Only the person detail and the `isReconciled`
      closure signal are unavailable, and a muted note says so.
    - **Empty.** Production has zero rows until ACCESS is repointed at SAM and
      `xras_action_log` starts filling. An empty card is a true report.

    PII is gated **here**, not in the template. Person detail — name, email,
    organization, academic status, residence country — is assembled only for a
    viewer holding MANAGE_XRAS, so a VIEW_XRAS response never carries it and a
    view-source cannot leak what the page chose not to draw. Same rule as the
    raw-payload panel and the notification recipients above.

    `is_reconciled` is deliberately on the VIEW_XRAS side of that line: it is a
    boolean about account state, not a personal detail, and it is the signal
    that tells an operator an item is about to close itself.
    """
    from sam.integration.xras_api import xras_api_configured

    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    window = _parse_activity_window(request.args)
    selected_classes = [c for c in request.args.getlist('classification') if c]
    selected_roles = [r for r in request.args.getlist('role') if r]
    selected_origins = [o for o in request.args.getlist('origin') if o]

    rows = get_account_worklist(db.session,
                                since=window['since'], until=window['until'])

    # ⚠️ Feed A ONLY, on purpose — this tab is precisely the accounts blocking
    # actions that have already POSTED, which is a claim we can always make
    # from our own audit table. The lookahead at what XRAS has approved but not
    # yet sent is the sibling tab, and it is contingent on the outbound API
    # being configured. Merging them would trade a guarantee for a maybe. The
    # union is available where it is actually needed — `sam-admin xras
    # --accounts`, and whatever digest comes after it.
    stamp_waiting_days(rows)

    # One query for the whole card, so the Request column can link the numbers
    # SAM already has a project for. Feed A only — the sibling Feed-B route
    # deliberately does not call this: its cohort is `numbers - known`, so
    # every row there is a number with no project by construction.
    stamp_project_existence(db.session, rows)

    # Enrichment is best-effort and never fatal: an outage or an unconfigured
    # deployment leaves `person` None and flags the batch, so the card degrades
    # to counts and usernames rather than returning 500.
    enrichment = enrich_worklist(rows, max_lookups=_ACCOUNTS_ENRICH_BUDGET)

    if not may_manage:
        # Drop the PII before it can reach a template, a log line, or a
        # response body. `is_reconciled` survives — see the docstring.
        for row in rows:
            row['person'] = None

    selected_requests = [r for r in request.args.getlist('request_number') if r]

    class_facets = _account_facets(rows, 'classification', roles=selected_roles)
    role_facets = _account_facets(rows, 'role', classifications=selected_classes)
    origin_facets = _account_facets(rows, 'origin',
                                    classifications=selected_classes,
                                    roles=selected_roles)
    request_facets = _request_facets(rows, classifications=selected_classes)

    rows = _filter_accounts(rows, classifications=selected_classes,
                            roles=selected_roles, origins=selected_origins)
    if selected_requests:
        # The operator working one project's activation wants only its rows.
        rows = [r for r in rows
                if {a['request_number'] for a in r['actions']} & set(selected_requests)]

    return render_template(
        'dashboards/allocations/partials/xras_accounts_card.html',
        rows=rows,
        counts=worklist_counts(rows),
        may_manage=may_manage,
        enrichment=enrichment,
        window=window,
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        # Both classifications render even at zero: an absent chip reads as
        # "not measured", a different claim from "none".
        classification_values=[
            {'value': key,
             'label': _ACCOUNT_CLASSIFICATION_LABELS.get(key, key),
             'count': class_facets.get(key, 0)}
            for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)],
        role_values=[{'value': k, 'count': v} for k, v in role_facets.items()],
        origin_values=[{'value': k, 'label': _ORIGIN_LABELS[k],
                        'count': origin_facets.get(k, 0)}
                       for k in (ORIGIN_PLACEHOLDER, ORIGIN_KNOWN)],
        request_values=request_facets,
        # What the sibling tab holds, so neither reads as the whole queue.
        # Counts only — never its rows, which would defeat the tab split.
        pending_total=_pending_account_total(),
        selected_classifications=selected_classes,
        selected_roles=selected_roles,
        selected_origins=selected_origins,
        selected_requests=selected_requests,
        form_id=_ACCOUNTS_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_accounts_fragment'),
        target_id=_ACCOUNTS_TARGET,
        # Gates the Request → detail-modal link (needs a live outbound read);
        # incoming-only degrades to today's project/plain cell. See request_cell.
        configured=xras_api_configured(),
    )


@bp.route('/xras_window_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_window_fragment():
    """HTMX fragment: just the shared window pills.

    ⚠️ **The control has to re-render itself, and this is why the route
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


@bp.route('/xras_pending_requests_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_pending_requests_fragment():
    """HTMX fragment: Feed B — XRAS requests SAM has no project for yet.

    **Read from a cache the `xras_sweep` task publishes, never computed
    here.** The enumeration behind this is 21 pages and 60-90 seconds against
    `api.xras.org`; no htmx round-trip can afford it, which is why the sweep
    is a producer and this is a consumer. The tab is therefore exactly as
    fresh as the last successful sweep, and it says so.

    Three distinct empty states, and conflating them would mislead:

    - **unconfigured** — `XRAS_OUTGOING_ENABLED` is off, so no sweep can run.
    - **no snapshot** — configured, but no sweep has published yet (the task
      may be disabled, or this is the first hour after a deploy).
    - **published and empty** — a real sweep found nothing pending, which is
      the healthy steady state.

    Same PII rule as the accounts tab: person detail only for MANAGE_XRAS,
    stripped in the route so a VIEW_XRAS response never carries it.
    """
    from sam.integration.xras_api import xras_api_configured
    from sam.integration.xras_api.cache import load_pending_worklist

    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    configured = xras_api_configured()
    snapshot = load_pending_worklist() if configured else None

    rows = list(snapshot.get('rows') or []) if snapshot else []
    # The snapshot is written by the task and read here, so the two can be on
    # different code — `stamp_waiting_days` backfills rather than assuming the
    # publisher already derived it. Stamped on read, never cached: an age is
    # the one field whose value depends on when you asked.
    stamp_waiting_days(rows)
    window = _parse_activity_window(request.args)
    selected_requests = [r for r in request.args.getlist('request_number') if r]
    selected_classes = [c for c in request.args.getlist('classification') if c]

    # ⚠️ A shared control that silently does nothing on one tab is worse than
    # no control, so the pills mean the same thing here as on the other two:
    # "what showed up in the last N days". For Feed B that is `submitDate`.
    #
    # Filtering on the period of performance was tried first and is wrong: a
    # pending request's allocation almost always ends a year out, so a
    # one-sided window keeps every row at every width and the pill looks dead
    # — the exact complaint this is fixing. The period of performance stays
    # where it belongs, bounding what the SWEEP collects; the header reports
    # that width so the two are never confused.
    rows = [r for r in rows if _submitted_since(r, window['since'])]

    if not may_manage:
        rows = [{**r, 'person': None} for r in rows]

    class_facets = _account_facets(rows, 'classification')
    request_facets = _request_facets(rows, classifications=selected_classes)

    rows = _filter_accounts(rows, classifications=selected_classes)
    if selected_requests:
        rows = [r for r in rows
                if {a['request_number'] for a in r['actions']} & set(selected_requests)]

    return render_template(
        'dashboards/allocations/partials/xras_pending_requests_card.html',
        rows=rows,
        snapshot=snapshot,
        configured=configured,
        may_manage=may_manage,
        counts=worklist_counts(rows),
        classification_values=[
            {'value': key,
             'label': _ACCOUNT_CLASSIFICATION_LABELS.get(key, key),
             'count': class_facets.get(key, 0)}
            for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)],
        request_values=request_facets,
        selected_classifications=selected_classes,
        selected_requests=selected_requests,
        form_id=_PENDING_FORM_ID,
        fragment_url=url_for(
            'allocations_dashboard.xras_pending_requests_fragment'),
        target_id=_PENDING_TARGET,
    )

