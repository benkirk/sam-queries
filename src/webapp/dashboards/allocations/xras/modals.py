"""XRAS read-only detail modals: request, user, and opportunity.

The three ``@bp.route`` detail-modal bodies plus their context builders. The
request-detail modal is the hub the write editors (``remediation``) hang off —
its ``_render_detail`` re-renders the modal in place after a write — so those
editors import it from here. This module imports only from ``_shared`` (never
from ``remediation``), which is what keeps the modals<->remediation graph
acyclic. Moved verbatim out of ``xras_remediation_routes.py``.
"""

from typing import Optional

from flask import current_app, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.users import User
from sam.integration.xras_api import (
    XrasSourceUnavailable,
    fos_name_map,
    get_opportunity,
    xras_admin_context_available,
    xras_api_configured,
    xras_write_configured,
)
from sam.queries.xras_accounts import is_placeholder, iter_roster_entries
from sam.integration.xras import lookup_request_override
from sam.queries.xras_requests import (
    _as_date,
    _text,
    actions_from_payload,
    person_roles_from_payload,
    request_family,
    request_index_entry,
    row_blockers,
)
from sam.schemas.forms.xras_remediation import XRAS_ACTION_TYPES
from webapp.extensions import db
from webapp.utils.htmx import htmx_modal_not_found
from webapp.utils.rbac import Permission, has_permission, require_permission

from .. import bp
from ._shared import (
    _degraded, _entry, _impersonation, _live_family,
    _primary_line, _read_client, _render_xras_modal, _role_options,
)


# ---------------------------------------------------------------------------
# read-only request detail (Part A) — the surface future editors hang off
# ---------------------------------------------------------------------------

_DETAIL_FORM = 'dashboards/allocations/partials/xras_request_detail.html'


#: The XRAS "stage" model, in the order the modal shows it: what was asked for,
#: what the panel recommended, what was awarded. Every ``resources[]`` entry in
#: a ``reports/request_numbers`` payload carries one of these as its ``type``,
#: and a resource appears once per stage — so grouping by stage is what makes
#: requested-vs-awarded legible at a glance.
_RESOURCE_STAGES = ('Requested', 'Recommended', 'Approved')


def _detail_actions(payload):
    """Per-action view model for the detail modal: a resource × stage matrix.

    Resources are **pivoted** — one row per resource, one column per stage that
    appears — so a resource present at several stages (the common case:
    Requested + Approved) is a single row with a cell per stage, rather than the
    same name repeated down three stacked stage lists. ``stages_present`` is the
    ordered subset of stages actually seen (Requested / Recommended / Approved,
    then a trailing ``Other`` for any unrecognised ``type``), so the template
    renders only the columns that exist. ``units`` is carried once per row (the
    same resource keeps its units across stages), which is what lets the stage
    columns hold a bare number.

    Built in Python because the pivot needs first-seen row order and a fixed
    column order together, which Jinja's ``groupby`` cannot express.
    """
    actions = []
    for action in payload.get('actions') or ():
        if not isinstance(action, dict):
            continue
        column_order = list(_RESOURCE_STAGES) + ['Other']
        present = {stage: False for stage in column_order}
        rows_by_key, row_order = {}, []
        for res in action.get('resources') or ():
            if not isinstance(res, dict):
                continue
            raw = res.get('type')
            stage = raw if raw in _RESOURCE_STAGES else 'Other'
            rid = res.get('resourceId')
            name = (res.get('displayResourceName') or res.get('resourceName')
                    or (('resource ' + str(rid)) if rid is not None
                        else 'resource'))
            # Key on the resource-type id (unique per resource); fall back to
            # the name only when a payload omits the id.
            key = rid if rid is not None else name
            row = rows_by_key.get(key)
            if row is None:
                row = {'resource_id': rid, 'name': name,
                       'units': res.get('resourceUnits') or '', 'cells': {}}
                rows_by_key[key] = row
                row_order.append(key)
            if not row['units']:
                row['units'] = res.get('resourceUnits') or ''
            row['cells'][stage] = {'amount': res.get('amount'),
                                   'comments': res.get('comments')}
            present[stage] = True
        stages_present = [s for s in column_order if present[s]]
        resource_rows = [rows_by_key[k] for k in row_order]
        for row in resource_rows:
            # Comments are rare and per (resource × stage); collect the
            # non-empty ones so the template can surface them under the row.
            row['comments'] = [(s, row['cells'][s]['comments'])
                               for s in column_order
                               if row['cells'].get(s)
                               and row['cells'][s].get('comments')]
        actions.append({
            'action_id': action.get('actionId'),
            'action_type': action.get('actionType'),
            'action_status': action.get('actionStatus'),
            'user_comments': action.get('userComments'),
            'admin_comments': action.get('adminComments'),
            'stages_present': stages_present,
            'resource_rows': resource_rows,
            # Dates arrive as raw ISO strings; parse to date objects here (the
            # same parser the entry builder uses) so the template can fmt_date
            # them — fmt_date raises on a str. `allocation_date_id` is carried
            # through so the edit/remove editors can target one range.
            'dates': [{'allocation_date_id': d.get('allocationDateId'),
                       'begin': _as_date(d.get('beginDate')),
                       'end': _as_date(d.get('endDate')),
                       # Wire key is allocationDateType (resources[] use `type`).
                       'type': d.get('allocationDateType')}
                      for d in (action.get('allocationDates') or ())
                      if isinstance(d, dict)],
            'documents': [d for d in (action.get('documents') or ())
                          if isinstance(d, dict)],
        })
    return actions


def _detail_grants(payload):
    """Grants with their raw ISO dates parsed for ``fmt_date``."""
    grants = []
    for g in payload.get('grants') or ():
        if not isinstance(g, dict):
            continue
        grants.append({**g,
                       'begin': _as_date(g.get('beginDate')),
                       'end': _as_date(g.get('endDate'))})
    return grants


def _actual_log_outcomes(action_ids):
    """Latest ``xras_action_log`` outcome per action_id, for the calibration view.

    Read straight from ``db.session`` (committed rows), keyed by ``action_id``:
    the modal shows the prediction beside what actually happened when XRAS pushed.
    """
    from sam.integration.xras import XrasActionLog

    ids = [i for i in action_ids if i is not None]
    if not ids:
        return {}
    outcomes = {}
    for row in (db.session.query(XrasActionLog)
                .filter(XrasActionLog.action_id.in_(set(ids)))
                .order_by(XrasActionLog.xras_action_log_id.asc()).all()):
        # Ascending id, so the last write for an action_id wins.
        outcomes[row.action_id] = {'status': row.status,
                                   'http_status': row.http_status,
                                   'error_messages': row.error_messages,
                                   'received_time': row.received_time,
                                   'raw_payload': row.raw_payload}
    return outcomes


def _detail_context(request_number, *, flash=None, flash_error=None):
    """Everything the detail modal renders, or ``None`` if the request is gone.

    Shared by the read-only GET and by every editor's success re-render, so the
    modal looks identical however it was reached. Raises
    :class:`XrasSourceUnavailable` on an outage — the caller degrades.
    """
    # The whole family in one fetch. A projcode can have several request lines
    # (a New plus Renewals, each its own requestId); the modal anchors on the
    # PRIMARY line (the one holding the globally most-recent action) so header,
    # roster and every write target the current request deterministically —
    # never lines[0], which is XRAS's arbitrary order.
    lines = _live_family(request_number)
    if not lines:
        return None
    payload = _primary_line(lines)
    family = request_family(lines)

    entry = _entry(request_number)
    # The preflight verdicts live in the sweep snapshot (keyed by actionId); the
    # modal reads a live payload, so carry them across rather than re-running the
    # never-writes preflight per modal open.
    preflights = {a['action_id']: a['preflight']
                  for a in (entry or {}).get('actions', ())
                  if a.get('preflight')}
    # Reproduces the exact `row` shape the roster editor expects (roster + PI),
    # from the primary line. `pending_push` only feeds the card's SAM badge,
    # which the include does not render — default it safely.
    row = request_index_entry(
        payload, pending_push=bool((entry or {}).get('pending_push', True)),
        preflights=preflights)
    _stamp_roster_targets(payload, row['roster'])

    # One action list spanning EVERY request line, chronological by action_id.
    # Only the single globally-most-recent action is editable/withdrawable; the
    # rest are applied history. `offers` (status-derived withdraw/resubmit) comes
    # from the line-scoped entry builder; the modal re-gates it on `is_latest`.
    offers = {a['action_id']: a
              for line in lines for a in actions_from_payload(line)}
    detail_actions = []
    for line in lines:
        for action in _detail_actions(line):
            o = offers.get(action['action_id'], {})
            action['request_id'] = line.get('requestId')
            action['request_type'] = _text(line.get('requestType'))
            action['entry_date'] = o.get('entry_date')
            action['submit_date'] = o.get('submit_date')
            action['can_withdraw'] = bool(o.get('can_withdraw'))
            action['can_resubmit'] = bool(o.get('can_resubmit'))
            detail_actions.append(action)
    detail_actions.sort(key=lambda a: a['action_id'])
    latest_id = detail_actions[-1]['action_id'] if detail_actions else None
    for action in detail_actions:
        action['is_latest'] = action['action_id'] == latest_id
        # Only the most-recent action may be edited; older ones are history.
        action['can_withdraw'] = action['can_withdraw'] and action['is_latest']
        action['can_resubmit'] = action['can_resubmit'] and action['is_latest']

    actuals = _actual_log_outcomes(a['action_id'] for a in detail_actions)
    for action in detail_actions:
        action['preflight'] = preflights.get(action['action_id'])
        # The real push outcome, if this action has since been posted — the
        # calibration comparison the request modal renders against the prediction.
        action['actual'] = actuals.get(action['action_id'])
    xa_user, is_pi, placeholder = _impersonation(entry, live=payload)

    request_id = (row or {}).get('request_id')
    return {
        'request_number': request_number,
        'payload': payload,
        'row': row,
        # Per-request operator overrides — same shared controls as the readiness
        # modal (row_blockers already treats a no-affiliation failure as mnemonic).
        'request_id': request_id,
        'blockers': row_blockers(row) if row else set(),
        'overrides': {kind: lookup_request_override(db.session, request_number, kind)
                      for kind in ('mnemonic', 'ignore_contract')},
        'can_edit_overrides': has_permission(current_user, Permission.ADMIN_XRAS),
        # The whole project lifecycle (all request lines + counts), for the
        # danger-zone delete confirm and any family-level display.
        'family': family,
        'detail_actions': detail_actions,
        'grants': _detail_grants(payload),
        'xa_user': xa_user,
        'xa_user_is_pi': is_pi,
        'xa_user_is_placeholder': placeholder,
        'write_enabled': xras_write_configured(),
        # Resolve the request's id-only fos[] to names. A reports payload spells
        # fos as {fosTypeId, fosNum} with no name, so without this the modal
        # renders "FoS 30". Best-effort (empty on outage) — a FoS name is a
        # nicety, never worth failing the request view for. Cached a day.
        'fos_names': fos_name_map(),
        'configured': xras_api_configured(),
        # Approved/Recommended editors render disabled until the elevated XRAS
        # key lands (Phase 0.5); this is the flip-point flag.
        'admin_context_available': xras_admin_context_available(),
        # The destructive lifecycle buttons render only for ADMIN_XRAS holders
        # (Part C) — effectively SYSTEM_ADMIN. A MANAGE_XRAS operator never sees
        # them, and the routes 403 anyway.
        'is_xras_admin': has_permission(current_user, Permission.ADMIN_XRAS),
        'action_types': list(XRAS_ACTION_TYPES),
        # The roster editor is inline in the modal now (no separate Roles form):
        # `row.roster` already carries `role_id` (roster_from_payload), and these
        # two feed the add-role control below it.
        'role_options': _role_options(),
        'role_add_url': url_for('allocations_dashboard.xras_role_add',
                                request_number=request_number),
        'flash': flash,
        'flash_error': flash_error,
    }


@bp.route('/xras_request_detail/<path:request_number>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_request_detail(request_number: str):
    """Modal body: the full detail of one request, read-only plus the editors.

    Renders resources grouped by XRAS stage (Requested / Recommended /
    Approved) so requested-vs-awarded is visible, the rich request sections
    (abstract, FoS, grants, documents), and — via the shared
    ``_xras_remediation_actions`` include — the roster and write buttons. This
    modal is the single opener the Remediations card's Request cell links to
    (the old per-request expansion was folded into it). The Requested-stage
    rows carry the amount/date editors (Part B); the Approved editors render
    fail-visible until the elevated key lands.

    Degrades with a **200** on an XRAS outage, like every modal GET here: htmx
    will not swap a 4xx into an already-open modal.
    """
    return _render_xras_modal(
        build=lambda: _detail_context(request_number),
        template=_DETAIL_FORM, noun='request',
        not_found=lambda: htmx_modal_not_found('Request'),
        log_label='xras request detail')


def _render_detail(request_number, *, flash=None, flash_error=None):
    """Re-render the detail modal in place after a write, with a flash.

    The write is already done, verified and recorded by the time this runs, so
    an outage here only costs the operator a fresh view — the change stands.
    """
    try:
        context = _detail_context(request_number, flash=flash,
                                  flash_error=flash_error)
    except XrasSourceUnavailable:
        context = None
    if context is None:
        note = flash or flash_error or 'Done.'
        cls = 'alert-danger' if flash_error else 'alert-success'
        return (f'<div class="alert {cls} mb-0">{note} '
                'The card may lag until the next hourly sweep.</div>', 200)
    response = current_app.make_response(render_template(_DETAIL_FORM, **context))
    response.headers['HX-Trigger'] = 'refreshXrasTab'
    return response


# ---------------------------------------------------------------------------
# focused push-readiness modal — the verdict badge's own evidence view
# ---------------------------------------------------------------------------

_READINESS_FORM = 'dashboards/allocations/partials/xras_readiness_modal.html'


def _readiness_context(request_number):
    """The focused readiness modal's context, or ``None`` if the sweep holds no
    such entry. Snapshot-only — the verdicts are already swept, so no live XRAS
    read; a committed-rows lookup adds the calibration outcome where one exists."""
    entry = _entry(request_number)
    if entry is None:
        return None
    actions = [a for a in (entry.get('actions') or []) if a.get('preflight')]
    actuals = _actual_log_outcomes(a.get('action_id') for a in actions)
    for action in actions:
        action['actual'] = actuals.get(action.get('action_id'))
    request_id = entry.get('request_id')
    blockers = row_blockers(entry)
    overrides = {kind: lookup_request_override(db.session, request_number, kind)
                 for kind in ('mnemonic', 'ignore_contract')}
    return {'request_number': request_number, 'request_id': request_id,
            'actions': actions, 'rollup': entry.get('preflight_rollup'),
            'blockers': blockers, 'overrides': overrides,
            'can_edit_overrides': has_permission(current_user, Permission.ADMIN_XRAS),
            'write_enabled': xras_write_configured()}


@bp.route('/xras_readiness_detail/<path:request_number>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_readiness_detail(request_number: str):
    """Modal body: the push-readiness evidence behind a request's verdict badge.

    Snapshot-only, so unlike the sibling detail modals it never needs a live XRAS
    read — it degrades to not-found only when the sweep has no such entry.
    """
    context = _readiness_context(request_number)
    if context is None:
        return htmx_modal_not_found('Request')
    return render_template(_READINESS_FORM, **context)


# ---------------------------------------------------------------------------
# read-only XRAS user detail — the analogue of the request detail modal
# ---------------------------------------------------------------------------

_USER_DETAIL_FORM = 'dashboards/allocations/partials/xras_user_detail.html'


def _merge_target_for(person) -> Optional[dict]:
    """``{'username', 'active'}`` of the SAM account holding *person*'s email, or None."""
    from sam.queries.xras_accounts import sam_merge_targets
    email = ((person or {}).get('email') or '').strip().lower()
    if not email:
        return None
    target = sam_merge_targets(db.session, [email]).get(email)
    if not target or not target.get('username'):
        return None
    return {'username': target['username'], 'active': target['active']}


def _stamp_roster_targets(payload, roster) -> None:
    """Stamp ``merge_target`` onto each placeholder roster row from the payload's emails."""
    from sam.queries.xras_accounts import sam_merge_targets
    emails = {}
    for person, _roles in iter_roster_entries(payload):
        username = (person.get('username') or '').strip()
        email = (person.get('email') or '').strip().lower()
        if username and email:
            emails[username.casefold()] = email
    targets = sam_merge_targets(db.session, emails.values())
    for member in roster:
        target = None
        if member.get('placeholder'):
            target = targets.get(emails.get((member.get('username') or '').casefold(), ''))
        member['merge_target'] = (
            {'username': target['username'], 'active': target['active']}
            if target and target.get('username') else None)


def _user_context(username, *, back_request_number=None):
    """Everything the XRAS User modal renders, or ``None`` if XRAS has no such
    person. Raises :class:`XrasSourceUnavailable` on an outage — caller degrades.

    Read-only apart from the reused merge: the only person-level write our key
    holds is the identity merge, offered for a placeholder that is either
    misidentified (reconciled yet still a placeholder) or unidentified with
    an active SAM account holding its email -- every active SAM user resolves
    in XRAS, so that account is a merge target already. ``merge_target``
    carries the matched username only; the email stays on ``person``.
    """
    person = _read_client().get_person(username)
    if person is None:
        return None

    # A merged-away or unknown placeholder 404s here just as get_person does;
    # person_roles_from_payload tolerates the resulting None.
    role_groups = person_roles_from_payload(
        _read_client().get_person_roles(username) or {})

    sam_user = User.get_by_username(db.session, username)
    placeholder = is_placeholder(username)
    is_reconciled = person.get('isReconciled')
    merge_target = _merge_target_for(person) if placeholder else None

    back_url = (url_for('allocations_dashboard.xras_request_detail',
                        request_number=back_request_number)
                if back_request_number else None)

    return {
        'username': username,
        'person': person,
        'role_groups': role_groups,
        # SAM cross-reference, informational only: the SAM user modal lives
        # inside its own #userDetailsModal, and opening it from within this
        # already-open #auditDetailsModal would stack two Bootstrap modals — an
        # idiom this page does not use. The Accounts-Needed card keeps the SAM
        # user opener in its non-modal home.
        'in_sam': sam_user is not None,
        'sam_active': bool(sam_user is not None and sam_user.is_active),
        'placeholder': placeholder,
        'is_reconciled': is_reconciled,
        'stuck_placeholder': bool(placeholder and is_reconciled),
        'merge_target': merge_target,
        'mergeable': bool(placeholder and (is_reconciled or
                                           (merge_target and merge_target['active']))),
        'merge_url': url_for('allocations_dashboard.xras_merge_form',
                             username=username),
        'back_url': back_url,
        'back_request_number': back_request_number,
        'write_enabled': xras_write_configured(),
        'configured': xras_api_configured(),
    }


@bp.route('/xras_user_detail/<path:username>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_user_detail(username: str):
    """Modal body: the XRAS-side person behind a username.

    The analogue of ``xras_request_detail`` — reached from any roster username
    (a plain ``hx-get`` into the open modal body, no ``data-bs-toggle``) and
    from the Accounts-Needed card's XRAS-identity column (``data-bs-toggle``
    from the closed modal). Person detail is PII, so ``MANAGE_XRAS`` gates it,
    matching the sibling cards. Degrades with a **200** on an outage.
    """
    back = request.args.get('request_number') or None
    return _render_xras_modal(
        build=lambda: _user_context(username, back_request_number=back),
        template=_USER_DETAIL_FORM, noun='user',
        not_found=lambda: _degraded(
            f'XRAS has no account named "{username}". A placeholder is often '
            'gone because it was merged into a real identity; the card will '
            'catch up on the next sweep.', title='Unknown XRAS user'),
        log_label='xras user detail')


# ---------------------------------------------------------------------------
# read-only opportunity detail — the "what is this allocation call" modal
# ---------------------------------------------------------------------------

_OPPORTUNITY_DETAIL_FORM = \
    'dashboards/allocations/partials/xras_opportunity_detail.html'


def _opportunity_context(opportunity_id, *, back_request_number=None):
    """The opportunity modal's context, or ``None`` if XRAS has no such id.

    Read-only — an opportunity is an allocation call SAM never writes. Raises
    :class:`XrasSourceUnavailable` on an outage; the caller degrades.
    """
    opportunity = get_opportunity(opportunity_id)
    if opportunity is None:
        return None
    back_url = (url_for('allocations_dashboard.xras_request_detail',
                        request_number=back_request_number)
                if back_request_number else None)
    return {
        'opportunity_id': opportunity_id,
        'opportunity': opportunity,
        # Parsed here (like grants/dates) — the wire carries a raw ISO string
        # and the template's fmt_date raises on a str.
        'announcement_date': _as_date(opportunity.get('announcementDate')),
        'back_url': back_url,
        'back_request_number': back_request_number,
        'configured': xras_api_configured(),
    }


@bp.route('/xras_opportunity_detail/<int:opportunity_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_opportunity_detail(opportunity_id: int):
    """Modal body: the allocation opportunity behind a request's header.

    Reached from the Remediations group header (``data-bs-toggle`` from the
    closed modal) and the request detail header (a plain ``hx-get`` into the
    open body, carrying ``request_number`` for a Back link). Read-only, so no
    write lever — but it still degrades with a **200** on an outage, like every
    modal GET here.
    """
    back = request.args.get('request_number') or None
    return _render_xras_modal(
        build=lambda: _opportunity_context(opportunity_id,
                                           back_request_number=back),
        template=_OPPORTUNITY_DETAIL_FORM, noun='opportunity',
        not_found=lambda: _degraded(
            f'XRAS has no opportunity #{opportunity_id}.',
            title='Unknown opportunity'),
        log_label='xras opportunity detail')

