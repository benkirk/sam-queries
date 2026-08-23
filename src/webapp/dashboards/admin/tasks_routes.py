"""Admin dashboard — scheduled-task run history.

The page an operator reaches from the Configuration tile's ``Details »``.
Structurally the notification delivery log, which is the same problem already
solved: facet chips with self-exclusion, a paginated table, a detail modal per
row. Both are built on ``querykit``.

⚠️ **The permission split is not the notifications one.** That page puts every
route at ``SYSTEM_ADMIN`` because every row names a real person's email
address. Task rows name task names, states and pod names — nothing personal —
so the page and its table are ``VIEW_SYSTEM_CONFIG``, the same tier as the
Configuration tile that links here. ``rate_limits_routes.py`` is the
same-tier precedent.

Only :func:`task_run_detail` sits higher: ``detail`` can hold a traceback
naming hosts, paths and connection strings, and ``runner_id`` is a pod name.
That one route is ``SYSTEM_ADMIN``, and the button that opens it renders only
for that tier — so the lower tier is never offered a control that 403s.

**Read-only.** No "run now" button: the Configuration tab is a read surface
and ``sam-admin tasks --run`` exists.

See ``docs/plans/implemented/SCHEDULED_TASKS_DASHBOARD.md``.
"""

import json
import logging

from flask import render_template, request, url_for
from flask_login import login_required
from sqlalchemy import select

from system_status.models.task_run import TASK_STATES, TASK_TRIGGERS, TaskRun
from system_status.queries.task_runs import (
    DEFAULT_WINDOW_HOURS,
    count_recent_task_runs,
    facet_task_runs,
    get_recent_task_runs,
    observed_task_names,
    summarize_task_runs,
)
from system_status.timeutil import utcnow_naive
from webapp.extensions import db
from webapp.utils.faceted_log import build_facet_strip, parse_window
from webapp.utils.htmx import htmx_modal_not_found
from webapp.utils.rbac import require_permission, Permission

from .blueprint import bp

logger = logging.getLogger(__name__)

#: The htmx form and swap target the facet chips write into.
_FORM_ID = 'scheduledTasksFilterForm'
_FRAGMENT_TARGET = 'scheduledTasksTableContainer'

#: Same default window as the notification log page.
_DEFAULT_DAYS = 30
_PER_PAGE = 50


def _ledger_missing():
    """True if `task_run` is absent, after clearing the failed transaction.

    ⚠️ Not hypothetical, and not only a test condition: `task_run` arrives with
    Alembic `0006`, which **staging and production have not applied**. The
    Configuration card already degrades for this (see `config_inspect`);
    without the same treatment here, the card's own `Details »` link leads to a
    500. CI found exactly that — its status database has no `task_run`, so the
    card said "unavailable" while this page threw.

    The rollback matters for the same reason it does in `config_inspect`: the
    failure came from a *statement*, so any later `db.session` use in this
    request would raise `PendingRollbackError` instead of its own error.
    """
    try:
        db.session.execute(select(TaskRun.task_run_id).limit(1)).first()
        return False
    except Exception:
        logger.info('task_run is not present on this database — '
                    'rendering the scheduled-tasks page in its degraded state')
        try:
            db.session.rollback()
        except Exception:                    # pragma: no cover - defensive
            pass
        return True


def _parse_filters(args):
    """Read the query string into ``(filters, page)``.

    ⚠️ ``now=utcnow_naive()`` is not optional. ``task_run`` is naive UTC while
    the notifications page this mirrors is naive Mountain; letting
    ``parse_window`` default to the local clock would shift the window by 6–7
    hours.
    """
    since, page = parse_window(args, default_days=_DEFAULT_DAYS,
                               per_page=_PER_PAGE, now=utcnow_naive())
    filters = {
        'since': since,
        'task_names': [t for t in args.getlist('task_name') if t],
        'states': [s for s in args.getlist('state') if s],
        'triggers': [t for t in args.getlist('trigger_type') if t],
        'search': (args.get('search', '') or '').strip() or None,
    }
    return filters, page


@bp.route('/htmx/tasks', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def scheduled_tasks():
    """The run-history page shell."""
    if _ledger_missing():
        return render_template('dashboards/admin/scheduled_tasks.html',
                               unavailable=True)
    return render_template(
        'dashboards/admin/scheduled_tasks.html',
        summary=summarize_task_runs(db.session),
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.scheduled_tasks_log'),
        all_task_names=observed_task_names(db.session),
        all_states=list(TASK_STATES),
        all_triggers=list(TASK_TRIGGERS),
        default_days=_DEFAULT_DAYS,
        window_hours=DEFAULT_WINDOW_HOURS,
    )


@bp.route('/htmx/tasks/log', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def scheduled_tasks_log():
    """HTMX fragment: the filtered, paginated table plus its facet chips."""
    if _ledger_missing():
        # A 200 carrying the explanation, not a 4xx/5xx: htmx will not swap a
        # non-2xx, so an error status leaves the spinner spinning for ever.
        return render_template(
            'dashboards/admin/fragments/scheduled_tasks_log.html',
            unavailable=True)

    filters, page = _parse_filters(request.args)
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_task_runs(db.session, **filters,
                                limit=page['per_page'], offset=offset)
    total = count_recent_task_runs(db.session, **filters)

    # Self-excluding rollups: each dimension's chips ignore its OWN filter
    # while honoring every other one. See querykit.facet_counts.
    state_counts = facet_task_runs(db.session, 'state', **filters)
    trigger_counts = facet_task_runs(db.session, 'trigger_type', **filters)
    task_counts = facet_task_runs(db.session, 'task_name', **filters)

    return render_template(
        'dashboards/admin/fragments/scheduled_tasks_log.html',
        rows=rows, total=total, page=page, filters=filters,
        # Declared vocabularies zero-fill and keep their order; task names have
        # no vocabulary (the registry is not what the rows say), so they sort
        # by count.
        state_facets=build_facet_strip(state_counts, TASK_STATES),
        trigger_facets=build_facet_strip(trigger_counts, TASK_TRIGGERS),
        task_facets=build_facet_strip(task_counts),
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.scheduled_tasks_log'),
    )


@bp.route('/htmx/tasks/<int:task_run_id>', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def task_run_detail(task_run_id: int):
    """Modal body: everything about one run, including its ``detail`` blob.

    One tier above the table on purpose — ``detail`` is where a traceback
    lands, and a traceback names hosts, paths and sometimes connection
    strings.
    """
    if _ledger_missing():
        return htmx_modal_not_found('Task run')

    row = db.session.get(TaskRun, task_run_id)
    if row is None:
        return htmx_modal_not_found('Task run')

    # `detail` is a JSON string in the column. TaskLedger._as_dict decodes it
    # for the CLI, but this route reads the ORM row directly, so decode here —
    # tolerantly. A blob that will not parse is still evidence, so it renders
    # as-is rather than being swallowed.
    detail = row.detail
    if detail:
        try:
            detail = json.dumps(json.loads(detail), indent=2, sort_keys=False)
        except (ValueError, TypeError):
            pass

    return render_template(
        'dashboards/admin/fragments/task_run_detail_modal.html',
        row=row, detail=detail)
