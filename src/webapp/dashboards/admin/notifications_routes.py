"""Admin dashboard — Notification delivery log.

The page an operator reaches from the Configuration tile's ``Details »``.
Structurally this is the XRAS action-log page, which is the same problem
already solved well: facet chips with self-exclusion, a sortable paginated
table, and a detail modal per row.

⚠️ **One permission tier above the tile, deliberately.** The Configuration
card is ``VIEW_SYSTEM_CONFIG`` and renders counts; **every row here names a
real person's email address**, so it is ``SYSTEM_ADMIN``. The gate is at the
route rather than in the template, so a view-source cannot reveal what the
page chose not to draw — the same rule ``get_xras_pending_recipients``
follows in the query layer.

There is deliberately **no `sam-admin` equivalent**, a divergence from the
XRAS precedent where CLI and web share a query layer *so the two cannot
drift*. ``sam/queries/notifications.py`` is still built as a shared layer —
the door stays open — but nothing on the CLI consumes it yet.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 8.
"""

import logging
from datetime import datetime, timedelta

from flask import render_template, request, url_for
from flask_login import login_required

from sam.notify import NOTIFICATION_KINDS, NOTIFICATION_STATUSES, NotifyConfig
from sam.notify.models import NotificationLog
from sam.queries.notifications import (
    count_recent_notifications,
    facet_notifications,
    get_recent_notifications,
    summarize_notifications,
)
from webapp.extensions import db
from webapp.utils.htmx import htmx_modal_not_found
from webapp.utils.rbac import require_permission, Permission

from .blueprint import bp

logger = logging.getLogger(__name__)

#: The htmx form and swap target the facet chips write into.
_FORM_ID = 'notificationsFilterForm'
_FRAGMENT_TARGET = 'notificationsTableContainer'

#: Same default window as the XRAS action-log page.
_DEFAULT_DAYS = 30
_PER_PAGE = 50


def _parse_filters(args):
    """Read the query string into ``(filters, page)``.

    Multi-valued dimensions come through ``getlist`` so a chip strip can
    express "status in (failed, suppressed)".
    """
    days = args.get('days', type=int) or _DEFAULT_DAYS
    days = max(1, min(days, 365))

    filters = {
        'since': datetime.now() - timedelta(days=days),
        'statuses': [s for s in args.getlist('status') if s],
        'kinds': [k for k in args.getlist('kind') if k],
        'channels': [c for c in args.getlist('channel') if c],
        'search': (args.get('search', '') or '').strip() or None,
    }
    page_n = max(1, args.get('page', type=int) or 1)
    return filters, {'n': page_n, 'per_page': _PER_PAGE, 'days': days}


@bp.route('/htmx/notifications', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notifications():
    """The delivery-log page shell."""
    config = NotifyConfig.from_environment()
    return render_template(
        'dashboards/admin/notifications.html',
        summary=summarize_notifications(
            db.session, queued_stale_seconds=config.queued_stale_seconds),
        config=config.summary(),
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
        all_statuses=list(NOTIFICATION_STATUSES),
        all_kinds=sorted(NOTIFICATION_KINDS),
        default_days=_DEFAULT_DAYS,
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
    # while honouring every other one. See facet_notifications.
    status_counts = facet_notifications(db.session, 'status', **filters)
    kind_counts = facet_notifications(db.session, 'kind', **filters)
    channel_counts = facet_notifications(db.session, 'channel', **filters)

    # Every declared status renders, including at zero — an absent bucket
    # reads as "not measured" rather than "none", and the strip is something
    # an operator scans by position. A status outside the vocabulary would be
    # a bad write; it appends rather than reshuffling.
    status_facets = [{'value': s, 'count': status_counts.get(s, 0)}
                     for s in NOTIFICATION_STATUSES]
    status_facets += [{'value': s, 'count': n}
                      for s, n in sorted(status_counts.items())
                      if s not in NOTIFICATION_STATUSES]

    kind_facets = [{'value': k, 'count': kind_counts.get(k, 0)}
                   for k in sorted(NOTIFICATION_KINDS)]
    kind_facets += [{'value': k, 'count': n}
                    for k, n in sorted(kind_counts.items())
                    if k not in NOTIFICATION_KINDS]

    channel_facets = sorted(
        ({'value': c, 'count': n} for c, n in channel_counts.items() if c),
        key=lambda r: (-r['count'], r['value']))

    return render_template(
        'dashboards/admin/fragments/notifications_log.html',
        rows=rows, total=total, page=page, filters=filters,
        status_facets=status_facets,
        kind_facets=kind_facets,
        channel_facets=channel_facets,
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
        has_more=offset + len(rows) < total,
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
