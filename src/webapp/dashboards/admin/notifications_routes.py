"""Admin dashboard — Notification delivery log.

The page an operator reaches from the Configuration tile's ``Details »``.
Structurally this is the XRAS action-log page, which is the same problem
already solved well: facet chips with self-exclusion, a sortable paginated
table, and a detail modal per row.

WARNING: **One permission tier above the tile, deliberately.** The Configuration
card is ``VIEW_SYSTEM_CONFIG`` and renders counts; **every row here names a
real person's email address**, so it is ``SYSTEM_ADMIN``. The gate is at the
route rather than in the template, so a view-source cannot reveal what the
page chose not to draw — the same rule ``get_xras_pending_recipients``
follows in the query layer.

There is deliberately **no `sam-admin` equivalent**, a divergence from the
XRAS precedent where CLI and web share a query layer *so the two cannot
drift*. ``sam/queries/notifications.py`` is still built as a shared layer —
the door stays open — but nothing on the CLI consumes it yet.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 8.
"""

import logging

from flask import abort, render_template, request, url_for
from flask_login import login_required

from sam.notify import NOTIFICATION_KINDS, NOTIFICATION_STATUSES, NotifyConfig
from sam.notify.models import NotificationLog
from sam.notify.render import TemplateRenderer, shipped_template_names
from sam.notify.samples import palette, preview_context
from sam.queries.notifications import (
    count_recent_notifications,
    facet_notifications,
    get_recent_notifications,
    summarize_notifications,
)
from webapp.extensions import db
from webapp.utils.faceted_log import build_facet_strip, parse_window
from webapp.utils.htmx import htmx_modal_not_found, read_tab
from webapp.utils.rbac import require_permission, Permission

from .blueprint import bp

logger = logging.getLogger(__name__)

#: The htmx form and swap target the facet chips write into.
_FORM_ID = 'notificationsFilterForm'
_FRAGMENT_TARGET = 'notificationsTableContainer'

#: Same default window as the XRAS action-log page.
_DEFAULT_DAYS = 30
_PER_PAGE = 50

_TABS = ('log', 'templates')
_PREVIEW_ROLES = ('lead', 'admin', 'user')
_EDITOR_TEMPLATE = 'dashboards/admin/fragments/notification_template_editor.html'


def _template_rows():
    """One row per shipped template file, with the kind and facility it serves."""
    rows = []
    for name in shipped_template_names():
        stem, fmt = name.rsplit('.', 1)
        kind = next((k for k in NOTIFICATION_KINDS.values()
                     if stem == k.template_base
                     or stem.startswith(k.template_base + '-')), None)
        if kind is None:
            continue
        rows.append({
            'name': name, 'stem': stem, 'fmt': fmt, 'kind': kind.key,
            'label': kind.label,
            'facility': stem[len(kind.template_base) + 1:] or None,
            'customized': None,
        })
    return rows


def _template_row_or_404(name):
    for row in _template_rows():
        if row['name'] == name:
            return row
    abort(404)


def _parse_filters(args):
    """Read the query string into ``(filters, page)``.

    Multi-valued dimensions come through ``getlist`` so a chip strip can
    express "status in (failed, suppressed)".
    """
    since, page = parse_window(args, default_days=_DEFAULT_DAYS,
                               per_page=_PER_PAGE)
    filters = {
        'since': since,
        'statuses': [s for s in args.getlist('status') if s],
        'kinds': [k for k in args.getlist('kind') if k],
        'channels': [c for c in args.getlist('channel') if c],
        'search': (args.get('search', '') or '').strip() or None,
    }
    return filters, page


@bp.route('/htmx/notifications', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notifications():
    """The page shell: the delivery log and the template editor, as tabs."""
    config = NotifyConfig.from_environment()
    template_rows = _template_rows()
    selected_name = request.args.get('name') or None
    if selected_name and selected_name not in {r['name'] for r in template_rows}:
        abort(404)
    # `?kind=` is the editor's deep link into the log, pre-filtered.
    preselect_kind = request.args.get('kind') or None
    if preselect_kind not in NOTIFICATION_KINDS:
        preselect_kind = None
    return render_template(
        'dashboards/admin/notifications.html',
        summary=summarize_notifications(
            db.session, queued_stale_seconds=config.queued_stale_seconds),
        config=config.summary(),
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
        initial_log_url=url_for('admin_dashboard.notifications_log',
                                kind=preselect_kind),
        preselect_kind=preselect_kind,
        all_statuses=list(NOTIFICATION_STATUSES),
        all_kinds=sorted(NOTIFICATION_KINDS),
        default_days=_DEFAULT_DAYS,
        active_tab=read_tab('tab', _TABS, 'log'),
        template_rows=template_rows,
        selected_name=selected_name,
    )


@bp.route('/htmx/notifications/log', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notifications_log():
    """HTMX fragment: the filtered, paginated table plus its facet chips."""
    filters, page = _parse_filters(request.args)
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_notifications(db.session, **filters,
                                    limit=page['per_page'], offset=offset)
    total = count_recent_notifications(db.session, **filters)

    # Self-excluding rollups: each dimension's chips ignore its OWN filter
    # while honoring every other one. See facet_notifications.
    status_counts = facet_notifications(db.session, 'status', **filters)
    kind_counts = facet_notifications(db.session, 'kind', **filters)
    channel_counts = facet_notifications(db.session, 'channel', **filters)

    # Zero-filled in vocabulary order, out-of-vocabulary appended. Channel has
    # no declared vocabulary, so it sorts by count instead of by position.
    status_facets = build_facet_strip(status_counts, NOTIFICATION_STATUSES)
    kind_facets = build_facet_strip(kind_counts, sorted(NOTIFICATION_KINDS))
    channel_facets = build_facet_strip(channel_counts)

    return render_template(
        'dashboards/admin/fragments/notifications_log.html',
        rows=rows, total=total, page=page, filters=filters,
        status_facets=status_facets,
        kind_facets=kind_facets,
        channel_facets=channel_facets,
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
    )


@bp.route('/htmx/notifications/<int:log_id>', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_detail(log_id: int):
    """Modal body: everything about one delivery attempt.

    Rendered bodies are **not** stored, so there is nothing to leak here
    beyond the columns — but the columns include the recipient, which is why
    this route carries the same ``SYSTEM_ADMIN`` gate as the table.
    """
    row = db.session.get(NotificationLog, log_id)
    if row is None:
        return htmx_modal_not_found('Notification')
    return render_template(
        'dashboards/admin/fragments/notification_detail_modal.html', row=row)


@bp.route('/htmx/notifications/templates/<name>', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_editor(name: str):
    """HTMX fragment: one template's source, its variables, and a preview pane."""
    row = _template_row_or_404(name)
    renderer = TemplateRenderer()
    source = renderer.env.loader.get_source(renderer.env, name)[0]
    return render_template(
        _EDITOR_TEMPLATE,
        row=row, source=source,
        variables=palette(row['kind'], row['facility']),
        template_rows=_template_rows(), selected_name=name,
        roles=_PREVIEW_ROLES,
        preview_url=url_for('admin_dashboard.notification_template_preview',
                            name=name),
        log_url=url_for('admin_dashboard.notifications', tab='log',
                        kind=row['kind']),
    )


@bp.route('/htmx/notifications/templates/<name>/preview', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_preview(name: str):
    """HTMX fragment: the posted body rendered against sample data.

    Always 200: htmx does not swap a 4xx/5xx, so a broken template must
    answer inside the pane, as an error panel, not as a dead button.
    """
    row = _template_row_or_404(name)
    body = request.form.get('body', '')
    role = request.form.get('role')
    if role not in _PREVIEW_ROLES:
        role = _PREVIEW_ROLES[0]
    context = preview_context(row['kind'], row['facility'], role)
    renderer = TemplateRenderer()
    result = {'row': row, 'error': None, 'warnings': [], 'text': None, 'html': None}
    try:
        result['warnings'] = sorted(renderer.undeclared_names(body, context))
        rendered = renderer.render_source(name, body, context)
    except Exception as exc:        # a template bug, whatever its type
        result['error'] = f'{type(exc).__name__}: {exc}'
    else:
        result['text' if row['fmt'] == 'txt' else 'html'] = rendered
    return render_template(
        'dashboards/admin/fragments/notification_template_preview.html',
        **result)
