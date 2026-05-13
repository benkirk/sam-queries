"""
Admin dashboard — Rate Limiting page.

Three independently-refreshable HTMX fragments backed by the webapp.limiter
event ring (Redis sorted-set, or per-worker deque fallback):

- ``recent``    — last N 429 events, filterable by actor substring + window
- ``offenders`` — top-N actors over 24h
- ``blocks``    — actors with a 429 inside the per-minute window (best-effort
                  "currently blocked" heuristic)

Plus an unblock POST that drops every storage key matching an actor; the
action is logged to the structured app log per CLAUDE.md's "long-term
forensics live in the structured app log" convention. No ORM tables touched,
so the audit subsystem (which traps SQLAlchemy events) doesn't apply here.
"""

import logging

from flask import render_template, request, current_app
from flask_login import login_required, current_user
from marshmallow import ValidationError

from sam.schemas.forms import ClearRateLimitForm
from webapp.limiter import limiter as _facade
from webapp.limiter.events import recent, top_offenders, active_blocks, clear_bucket
from webapp.utils.rbac import require_permission, Permission

from .blueprint import bp

logger = logging.getLogger(__name__)


_WINDOW_PRESETS = {
    '1h':  3600,
    '24h': 86400,
}


@bp.route('/htmx/rate-limits', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def rate_limits():
    """Render the dedicated rate-limits admin page."""
    return render_template(
        'dashboards/admin/rate_limits.html',
        stats=_facade.stats(),
    )


@bp.route('/htmx/rate-limits/recent', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def rate_limits_recent():
    """HTMX fragment: recent 429 events, optionally filtered."""
    actor_filter = (request.args.get('actor', '') or '').strip().lower()
    window = request.args.get('window', '24h')
    window_seconds = _WINDOW_PRESETS.get(window, 86400)

    import time
    from datetime import datetime
    cutoff = time.time() - window_seconds
    events = []
    for e in recent(limit=current_app.config['RATELIMIT_EVENT_MAX']):
        if e.get('ts', 0) < cutoff:
            continue
        if actor_filter and actor_filter not in e.get('actor', '').lower():
            continue
        # Enrich with a Python datetime so the template can render via the
        # standard fmt_date Jinja filter (per CLAUDE.md "Display Formatting").
        e = {**e, 'dt': datetime.fromtimestamp(e['ts'])}
        events.append(e)
    return render_template(
        'dashboards/admin/fragments/rate_limits_recent.html',
        events=events,
        actor_filter=actor_filter,
        window=window,
    )


@bp.route('/htmx/rate-limits/offenders', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def rate_limits_offenders():
    """HTMX fragment: top offenders over the last 24h."""
    ranked = top_offenders(window_seconds=86400, n=20)
    return render_template(
        'dashboards/admin/fragments/rate_limits_offenders.html',
        ranked=ranked,
    )


@bp.route('/htmx/rate-limits/blocks', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def rate_limits_blocks():
    """HTMX fragment: actors currently in a rate-limit block."""
    return render_template(
        'dashboards/admin/fragments/rate_limits_blocks.html',
        blocks=_blocks_with_dt(),
    )


def _blocks_with_dt():
    """Wrap active_blocks() entries with a Python datetime for fmt_date."""
    from datetime import datetime
    return [
        {**b, 'last_429_dt': datetime.fromtimestamp(b['last_429_ts'])}
        for b in active_blocks()
    ]


@bp.route('/htmx/rate-limits/unblock', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def rate_limits_unblock():
    """Drop every limiter bucket associated with the actor key.

    Best-effort SCAN+DEL across Redis storage keys (and any in-process
    memory:// limiter dict on this worker). Logs the action to the
    structured app log; returns the refreshed "blocks" fragment so the
    table reflects the change immediately.
    """
    try:
        data = ClearRateLimitForm().load(request.form)
    except ValidationError as e:
        errors = ClearRateLimitForm.flatten_errors(e.messages)
        logger.warning(
            "rate_limit_unblock_invalid actor_input=%r by=%s errors=%s",
            request.form.get('actor'), getattr(current_user, 'username', '?'), errors,
        )
        return render_template(
            'dashboards/admin/fragments/rate_limits_blocks.html',
            blocks=_blocks_with_dt(),
            errors=errors,
        ), 400

    actor = data['actor']
    removed = clear_bucket(actor)
    logger.info(
        "rate_limit_unblock actor=%s removed_keys=%d by=%s",
        actor, removed, getattr(current_user, 'username', '?'),
    )
    return render_template(
        'dashboards/admin/fragments/rate_limits_blocks.html',
        blocks=_blocks_with_dt(),
        message=f'Unblocked {actor} ({removed} key{"s" if removed != 1 else ""} cleared).',
    )
