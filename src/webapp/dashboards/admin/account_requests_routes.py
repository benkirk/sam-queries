"""Admin dashboard -- Accounts: the HPC account-request queue NUSD works.

One card in the XRAS worklist idiom: self-excluding facet chips, a search box,
a queue/everything switch, sortable headers held in the hidden filter form,
and one-click row actions that answer with an HX-Trigger. Every write runs
inside ``management_transaction``; the render never writes -- fulfillment is
derived per render and stamped only by Reconcile (button or hourly task).
Design: docs/plans/ACCOUNT_REGISTRATION.md section 3.2.
"""

import logging
import os
from datetime import datetime

from flask import current_app, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.account_requests import (
    ACCOUNT_REQUEST_PURPOSES,
    ACCOUNT_REQUEST_STATES,
    CREATED_BY_SELF,
    CREATED_BY_SWEEP,
    AccountRequest,
)
from sam.core.users import User
from sam.manage import management_transaction
from sam.manage.account_requests import reconcile_account_requests
from sam.projects.projects import Project
from sam.queries.account_requests import (
    all_requests,
    build_queue_summary,
    events_for,
    queue_counts,
    queue_requests,
    readiness_of,
    resolve_requests,
    unverified_count,
    waiting_days,
)
from sam.schemas.forms import AccountRequestReasonForm
from webapp.extensions import db
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_modal_not_found, htmx_not_found, htmx_success,
    htmx_success_message, modal_triggers, read_flag, read_sort,
)
from webapp.utils.notify import get_notifier
from webapp.utils.rbac import Permission, require_permission

from .blueprint import bp

logger = logging.getLogger(__name__)

_FORM_ID = 'account-requests-filters'
_TARGET = 'account-requests-card'
_CARD = 'dashboards/admin/fragments/account_requests_card.html'
_REASON_FORM = 'dashboards/admin/fragments/account_request_reason_form_htmx.html'
# Both cards that show these rows: the Accounts queue and the XRAS Pending
# Users card, which posts to these same routes from the allocations page.
_TRIGGERS = {'refreshAccountQueue': {}, 'refreshXrasTab': {}}
_MODAL_TRIGGERS = modal_triggers('refreshAccountQueue', 'refreshXrasTab')

ORIGIN_SELF, ORIGIN_SPONSOR, ORIGIN_SWEEP = 'self', 'sponsor', 'sweep'
_ORIGIN_LABELS = {ORIGIN_SELF: 'Public form', ORIGIN_SPONSOR: 'Invited',
                  ORIGIN_SWEEP: 'XRAS roster'}
_READINESS_LABELS = {'open': 'No account yet', 'ready': 'Account exists',
                     'inactive': 'Account inactive', 'ambiguous': 'Ambiguous email',
                     'failed': 'Enrollment failed', 'fulfilled': 'Fulfilled'}
_READINESS_ORDER = ('ready', 'failed', 'inactive', 'ambiguous', 'open', 'fulfilled')
_FACETS = ('state', 'purpose', 'origin', 'readiness', 'event')

_SORT = {
    'person': lambda r: (r['last_name'].casefold(), r['first_name'].casefold()),
    'email': lambda r: r['email'],
    'waiting': lambda r: r['waiting_days'],
    'deadline': lambda r: r['deadline'],
    'state': lambda r: r['state'],
}


def _origin_of(row: AccountRequest) -> str:
    if row.created_by == CREATED_BY_SELF:
        return ORIGIN_SELF
    if row.created_by == CREATED_BY_SWEEP:
        return ORIGIN_SWEEP
    return ORIGIN_SPONSOR


def _views(rows, resolutions, events):
    """One plain dict per row: what the card and the sort keys read."""
    sponsor_ids = sorted({r.sponsor_user_id for r in rows if r.sponsor_user_id})
    sponsors = ({u.user_id: u for u in db.session.query(User)
                 .filter(User.user_id.in_(sponsor_ids)).all()}
                if sponsor_ids else {})
    project_ids = sorted({r.project_id for r in rows if r.project_id}
                         | {e.project_id for e in events.values()})
    projects = (dict(db.session.query(Project.project_id, Project.projcode)
                     .filter(Project.project_id.in_(project_ids)).all())
                if project_ids else {})
    today = datetime.now().date()
    views = []
    for r in rows:
        event = events.get(r.event_id) if r.event_id else None
        resolution = resolutions.get(r.account_request_id)
        sponsor = sponsors.get(r.sponsor_user_id)
        views.append({
            'id': r.account_request_id,
            'row': r,
            'first_name': r.first_name, 'last_name': r.last_name,
            'name': r.display_name, 'email': r.email,
            'state': r.state, 'purpose': r.purpose,
            'origin': _origin_of(r),
            'readiness': readiness_of(r, resolution),
            'resolution': resolution,
            'event': event,
            'event_code': event.event_code if event else '',
            'deadline': event.accounts_needed_by if event else None,
            'project_code': projects.get(r.project_id, '') if r.project_id else '',
            'event_project_code': projects.get(event.project_id, '') if event else '',
            'sponsor': sponsor,
            'waiting_days': waiting_days(r, today=today),
            'verified': r.is_verified,
        })
    return views


def _apply(views, selected, *, skip=None):
    """The chip selections, ANDed across dimensions; ``skip`` one for its facet."""
    out = views
    for dim in _FACETS:
        if dim == skip or not selected.get(dim):
            continue
        wanted = set(selected[dim])
        key = 'event_code' if dim == 'event' else dim
        out = [v for v in out if v[key] in wanted]
    return out


def _facet(views, selected, dim, order=None, labels=None):
    scoped = _apply(views, selected, skip=dim)
    key = 'event_code' if dim == 'event' else dim
    counts = {}
    for v in scoped:
        counts[v[key]] = counts.get(v[key], 0) + 1
    if order is None:
        order = sorted(k for k in counts if k)
    return [{'value': k, 'count': counts.get(k, 0),
             'label': (labels or {}).get(k, k)}
            for k in order if k and (counts.get(k) or k in (selected.get(dim) or ()))]


def _search(views, term):
    if not term:
        return views
    needle = term.casefold()
    return [v for v in views if needle in ' '.join((
        v['name'], v['email'], v['row'].organization or '',
        v['row'].desired_username or '', v['row'].xras_username or '',
        v['project_code'], v['event_code'],
        v['sponsor'].display_name if v['sponsor'] else '')).casefold()]


def _group(views, events):
    """Event groups nearest deadline first, then the rest in the sorted order."""
    by_event = {}
    loose = []
    for v in views:
        (by_event.setdefault(v['event'].account_request_event_id, []) if v['event']
         else loose).append(v)
    groups = [{'event': events[eid], 'rows': members,
               'project_code': members[0]['event_project_code']}
              for eid, members in by_event.items()]
    groups.sort(key=lambda g: (g['event'].accounts_needed_by, g['event'].event_code))
    if loose:
        groups.append({'event': None, 'rows': loose, 'project_code': ''})
    return groups


def _sort_views(views, sort):
    keyfn = _SORT.get((sort or {}).get('sort_by'))
    if not keyfn:
        return views
    reverse = (sort or {}).get('sort_dir') == 'desc'
    present = [v for v in views if keyfn(v) is not None]
    absent = [v for v in views if keyfn(v) is None]
    present.sort(key=keyfn, reverse=reverse)
    return present + absent


@bp.route('/account-requests')
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_requests():
    """The Accounts page: one card, loaded by htmx."""
    return render_template(
        'dashboards/admin/account_requests.html',
        form_id=_FORM_ID, target_id=_TARGET,
        fragment_url=url_for('admin_dashboard.account_requests_fragment'),
        facets=_FACETS,
    )


@bp.route('/account-requests/fragment')
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_requests_fragment():
    """The queue card. Derives readiness per render; writes nothing."""
    show_all = read_flag(request.args, 'show_all')
    queue = queue_requests(db.session)
    rows = all_requests(db.session) if show_all else queue
    resolutions = resolve_requests(db.session, rows)
    events = events_for(db.session, rows)
    counts = queue_counts(queue, resolutions if show_all
                          else resolve_requests(db.session, queue))
    views = _views(rows, resolutions, events)
    scoped_total = len(views)

    search = (request.args.get('search') or '').strip()
    views = _search(views, search)
    selected = {dim: [x for x in request.args.getlist(dim) if x] for dim in _FACETS}
    facet_values = {
        'state': _facet(views, selected, 'state', ACCOUNT_REQUEST_STATES),
        'purpose': _facet(views, selected, 'purpose', ACCOUNT_REQUEST_PURPOSES),
        'origin': _facet(views, selected, 'origin', tuple(_ORIGIN_LABELS),
                         _ORIGIN_LABELS),
        'readiness': _facet(views, selected, 'readiness', _READINESS_ORDER,
                            _READINESS_LABELS),
        'event': _facet(views, selected, 'event'),
    }
    views = _apply(views, selected)
    sort = read_sort(request.args, _SORT, default_dir='asc')
    views = _sort_views(views, sort)

    return render_template(
        _CARD,
        groups=_group(views, events),
        total=len(views), scoped_total=scoped_total,
        counts=counts, unverified=unverified_count(db.session),
        show_all=show_all, search=search,
        facet_values=facet_values, selected=selected,
        origin_labels=_ORIGIN_LABELS, readiness_labels=_READINESS_LABELS,
        sort=sort, sortable_columns=set(_SORT),
        form_id=_FORM_ID, target_id=_TARGET,
        fragment_url=url_for('admin_dashboard.account_requests_fragment'),
        digest_recipient=_digest_recipient(),
    )


def _load(request_id):
    return db.session.get(AccountRequest, int(request_id))


def _toast_error(message):
    """A danger toast for an ``hx-swap="none"`` button; no reload trigger."""
    return htmx_success('dashboards/fragments/htmx_success.html', {},
                        toast=message, toast_variant='danger', message=message)


def _one_click(request_id, verb, action, done):
    row = _load(request_id)
    if row is None:
        return htmx_not_found('Account request')
    try:
        with management_transaction(db.session):
            action(row)
    except ValueError as exc:
        return _toast_error(f'Cannot {verb}: {exc}')
    return htmx_success_message(_TRIGGERS, done.format(name=row.display_name))


@bp.route('/account-requests/<int:request_id>/claim', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_claim(request_id):
    return _one_click(request_id, 'claim',
                      lambda row: row.claim(current_user.username),
                      'Claimed {name}.')


@bp.route('/account-requests/<int:request_id>/unclaim', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_unclaim(request_id):
    return _one_click(request_id, 'unclaim', lambda row: row.unclaim(),
                      'Released {name}.')


@bp.route('/account-requests/<int:request_id>/verify', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_verify(request_id):
    """An operator vouching for an address the mail round trip did not confirm."""
    return _one_click(request_id, 'verify',
                      lambda row: row.mark_verified(current_user.username),
                      'Marked {name} verified; the request is now in the queue.')


@bp.route('/account-requests/<int:request_id>/reopen', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_reopen(request_id):
    return _one_click(request_id, 'reopen', lambda row: row.reopen(),
                      'Reopened {name}.')


def _reason_form(request_id, verb):
    row = _load(request_id)
    if row is None:
        return htmx_modal_not_found('Account request')
    return render_template(_REASON_FORM, row=row, verb=verb,
                           post_url=url_for(f'admin_dashboard.account_request_{verb}',
                                            request_id=request_id))


def _reason_post(request_id, verb, action, done):
    row = _load(request_id)
    if row is None:
        return htmx_not_found('Account request')
    return handle_htmx_form_post(
        schema_cls=AccountRequestReasonForm,
        template=_REASON_FORM,
        do_action=lambda data: action(row, data['reason']),
        success_triggers=_MODAL_TRIGGERS,
        success_message=done.format(name=row.display_name),
        error_prefix=f'Error: could not {verb}',
        extra_context={
            'row': row, 'verb': verb,
            'post_url': url_for(f'admin_dashboard.account_request_{verb}',
                                request_id=request_id),
        },
    )


@bp.route('/account-requests/<int:request_id>/dismiss-form')
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_dismiss_form(request_id):
    return _reason_form(request_id, 'dismiss')


@bp.route('/account-requests/<int:request_id>/dismiss', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_dismiss(request_id):
    """Set aside: a duplicate, or someone who already has an account."""
    return _reason_post(request_id, 'dismiss',
                        lambda row, reason: row.dismiss(current_user.username, reason),
                        'Dismissed {name}.')


@bp.route('/account-requests/<int:request_id>/reject-form')
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_reject_form(request_id):
    return _reason_form(request_id, 'reject')


@bp.route('/account-requests/<int:request_id>/reject', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_request_reject(request_id):
    """Refuse; the reason is what the requester is told."""
    return _reason_post(request_id, 'reject',
                        lambda row, reason: row.reject(current_user.username, reason),
                        'Rejected {name}.')


@bp.route('/account-requests/reconcile', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_requests_reconcile():
    """Reconcile now: the same pass the hourly task runs. No purge here."""
    with management_transaction(db.session):
        counts = reconcile_account_requests(db.session, clock=datetime.now())
    return htmx_success_message(
        _TRIGGERS,
        f"Reconciled: {counts['fulfilled']} fulfilled, {counts['enrolled']} "
        f"enrolled, {counts['enroll_failed']} enrollment failure(s).",
        detail=(f"{counts['checked']} checked; {counts['ambiguous']} ambiguous "
                f"email(s) left alone."))


def _digest_recipient() -> str:
    raw = current_app.config.get('NOTIFY_ACCOUNT_QUEUE_TO') or \
        os.environ.get('NOTIFY_ACCOUNT_QUEUE_TO') or ''
    return str(raw).strip()


@bp.route('/account-requests/digest', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ACCOUNT_REQUESTS)
def account_requests_digest():
    """Send the queue digest to NUSD now. Same builder and key as the task."""
    recipient = _digest_recipient()
    if not recipient:
        return _toast_error('No digest recipient is configured '
                            '(NOTIFY_ACCOUNT_QUEUE_TO).')
    now = datetime.now()
    with management_transaction(db.session):
        reconcile_account_requests(db.session, clock=now)
    rows = queue_requests(db.session)
    if not rows:
        return _toast_error('The queue is empty; nothing to send.')
    message = build_queue_summary(
        db.session, rows, recipient=recipient, occurrence=now,
        requested_by=current_user.username,
        queue_url=url_for('admin_dashboard.account_requests', _external=True))
    result = get_notifier().send(message)
    if result.status in ('sent', 'redirected'):
        with management_transaction(db.session):
            for row in rows:
                row.mark_requested(now)
        return htmx_success_message(
            _TRIGGERS, f'Digest of {len(rows)} request(s) {result.status} to {recipient}.')
    if result.status == 'suppressed':
        return _toast_error('A digest already went to NUSD today; not sent again.')
    return _toast_error(f'Digest not sent: {result.detail or result.status}.')
