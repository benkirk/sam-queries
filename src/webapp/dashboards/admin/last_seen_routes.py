"""Admin -> Users & Groups -> Last seen: SAM users by their newest ledger sighting.

Two databases, joined in Python by username (``sam.queries.last_seen_review``):
both sides load whole, since the ledger is one row per (user, source). A status
DB outage renders "unavailable" with a 200, because htmx will not swap a non-2xx.
Record: ``docs/plans/implemented/USER_LAST_SEEN.md``.
"""

import logging

from flask import render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from sam.queries.last_seen_review import BUCKET_KEYS, BUCKETS, NOT_IN_SAM, review
from sam.queries.users import count_active_projects_by_username, get_user_directory
from system_status.models.last_seen import SOURCE_KINDS, UserLastSeen
from system_status.queries.last_seen import get_last_seen_by_user
from system_status.timeutil import utcnow_naive
from webapp.extensions import db
from webapp.utils.faceted_log import build_facet_strip
from webapp.utils.htmx import (
    read_active_only, read_flag, read_multi, read_page, read_sort,
)
from webapp.utils.rbac import Permission, require_permission_any_facility

from .blueprint import bp

logger = logging.getLogger(__name__)

_FORM_ID = 'lastSeenFilterForm'
_TARGET_ID = 'lastSeenTableContainer'
_PER_PAGE = 50
_SORTABLE = ('username', 'name', 'last_seen')


def ledger_missing():
    """True if the ledger cannot be read, after clearing the failed transaction."""
    try:
        db.session.execute(select(UserLastSeen.user_id).limit(1)).first()
        return False
    except Exception:
        logger.warning('user_last_seen unreadable; rendering the Last seen page degraded',
                       exc_info=True)
        db.session.rollback()
        return True


def _filters(args):
    kind = args.get('kind') or None
    sort = read_sort(args, _SORTABLE)
    return {
        'bucket': [b for b in read_multi(args, 'bucket') if b in BUCKET_KEYS],
        'kind': kind if kind in SOURCE_KINDS else None,
        'search': (args.get('q') or '').strip() or None,
        'sort_by': sort['sort_by'] or 'last_seen',
        'sort_dir': sort['sort_dir'],
    }


@bp.route('/users/last-seen')
@login_required
@require_permission_any_facility(Permission.VIEW_USERS)
def users_last_seen():
    """Page shell; the query string seeds the filter form so links are shareable."""
    return render_template(
        'dashboards/admin/users_last_seen.html',
        user=current_user,
        filters=_filters(request.args),
        active_only=read_flag(request.args, 'active_only', True),
        form_id=_FORM_ID,
        target_id=_TARGET_ID,
        fragment_url=url_for('admin_dashboard.users_last_seen_table'),
    )


@bp.route('/htmx/users/last-seen')
@login_required
@require_permission_any_facility(Permission.VIEW_USERS)
def users_last_seen_table():
    """HTMX fragment: bucket and source chips, the sorted page, pagination."""
    if ledger_missing():
        return render_template('dashboards/admin/fragments/users_last_seen_table.html',
                               unavailable=True)

    filters = _filters(request.args)
    active_only = read_active_only(request.args)
    page_n = read_page(request.args)['n']

    rows, bucket_counts, kind_counts = review(
        get_user_directory(db.session, active_only=active_only),
        get_last_seen_by_user(db.session),
        now=utcnow_naive(), include_unlisted=not active_only, **filters)

    start = (page_n - 1) * _PER_PAGE
    page_rows = rows[start:start + _PER_PAGE]
    projects = count_active_projects_by_username(
        db.session, [r.username for r in page_rows if r.status != NOT_IN_SAM])

    labels = {b.key: b.label for b in BUCKETS}
    bucket_facets = build_facet_strip(bucket_counts, BUCKET_KEYS)
    for f in bucket_facets:
        f['label'] = labels.get(f['value'])

    return render_template(
        'dashboards/admin/fragments/users_last_seen_table.html',
        rows=page_rows,
        total=len(rows),
        projects=projects,
        filters=filters,
        bucket_facets=bucket_facets,
        kind_facets=build_facet_strip(kind_counts, SOURCE_KINDS),
        page={'n': page_n, 'per_page': _PER_PAGE},
        sort={'sort_by': filters['sort_by'], 'sort_dir': filters['sort_dir']},
        sortable_columns=_SORTABLE,
        form_id=_FORM_ID,
        target_id=_TARGET_ID,
        fragment_url=url_for('admin_dashboard.users_last_seen_table'),
    )
