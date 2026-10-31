"""``xras_rename`` — rename XRAS requests still under their ``NCAR####`` token to the projcode.

Hourly. The webapp renames a line right after its post lands
(``webapp/api/xras/actions.py``); this catches what that missed: a failed attempt, a
pod gone mid-call, the write lever off at the time. ``--dry-run`` reads and reports
what it would do, and needs only the read config. ``SAM_TASKS_XRAS_RENAME_SINCE``
(an ISO date) widens the window once, for the production backfill.

WARNING: a scheduled task that writes to XRAS (``TASK_WRITABLE_OPERATIONS``; the other is
``account_requests_reconcile``'s placeholder merge).
It ships named in production's ``SAM_TASKS_DISABLED``, and production's task env
carries no ``XRAS_WRITE_ENABLED``: two locks. Design: ``docs/plans/XRAS_CLOSED_LOOP.md``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import Hourly, to_local_naive
from scheduling.tasks._notice_common import new_sam_session

#: Dispatched at the CronJob's :22 wake.
SCHEDULE = Hourly(minute=20)

#: The window's floor: a blast-radius bound, not a retry budget.
LOOKBACK_DAYS = 14
SINCE_ENV = 'SAM_TASKS_XRAS_RENAME_SINCE'
OPERATOR = 'task:xras_rename'
_MAX_REPORTED = 100
_TZ = ZoneInfo('America/Denver')


def window_start(occurrence: datetime, env: Optional[dict] = None) -> datetime:
    """Naive-Mountain floor on ``received_time``; a malformed override raises, visibly."""
    raw = ((env if env is not None else os.environ).get(SINCE_ENV) or '').strip()
    if raw:
        return datetime.fromisoformat(raw)
    return to_local_naive(occurrence, _TZ) - timedelta(days=LOOKBACK_DAYS)


def _note(detail: Dict[str, Any], key: str, item: Dict[str, Any]) -> None:
    detail[key] += 1
    if len(detail['sample']) < _MAX_REPORTED:
        detail['sample'].append({**item, 'state': key})


@task(name='xras_rename', schedule=SCHEDULE, needs=('sam',),
      expected_runtime=timedelta(minutes=5),     # lease knob; > activeDeadlineSeconds
      description='Rename XRAS requests still under their NCAR#### token to the projcode')
def xras_rename(ctx) -> TaskResult:
    from sam.integration.xras_api.base import XrasSourceUnavailable
    from sam.integration.xras_api.config import xras_api_configured, xras_write_configured
    from sam.manage import xras_remediation as remediation
    from sam.queries.xras_actions import rename_candidates
    from sam.queries.xras_requests import live_lines

    detail: Dict[str, Any] = {
        'since': None, 'candidates': 0, 'rename': 0, 'verified': 0, 'already_renamed': 0,
        'siblings': 0, 'skipped': 0, 'no_role_holder': 0, 'failed': 0, 'sample': [],
    }
    if not xras_api_configured():
        detail['skipped_run'] = 'XRAS outgoing API not configured'
        return TaskResult(detail=detail, message='skipped — XRAS outgoing API not configured')
    if not ctx.dry_run and not xras_write_configured():
        detail['skipped_run'] = 'XRAS_WRITE_ENABLED is off'
        return TaskResult(detail=detail, message='skipped — XRAS_WRITE_ENABLED is off')

    since = window_start(ctx.occurrence)
    detail['since'] = since.isoformat()
    candidates = rename_candidates(ctx.sam_session, since=since)
    detail['candidates'] = len(candidates)

    if ctx.dry_run:
        from sam.integration.xras_api.client import XrasApiClient
        reader = XrasApiClient.from_environment()
        for c in candidates:
            item = {k: c[k] for k in ('request_id', 'request_number', 'projcode')}
            try:
                state, xa_user, _ = remediation.rename_plan(
                    reader.get_request_by_id(c['request_id']), request_id=c['request_id'],
                    old_number=c['request_number'], new_number=c['projcode'])
            except XrasSourceUnavailable as exc:
                _note(detail, 'failed', {**item, 'error': str(exc)})
                continue
            _note(detail, state, {**item, 'xa_user': xa_user})
        return TaskResult(detail=detail, partial_failures=detail['failed'],
                          message=f"dry run: {detail['rename']} of {len(candidates)} would rename")

    from sam.integration.xras_api.admin_client import XrasAdminClient
    admin = XrasAdminClient.from_environment()

    def factory():
        return new_sam_session(ctx.sam_session)

    def rename(request_id, old, new, item):
        outcome = remediation.rename_request(factory, request_id=request_id, old_number=old,
                                             new_number=new, operator=OPERATOR, client=admin)
        key = outcome.status if outcome.status in detail else 'failed'
        _note(detail, key, {**item, 'event_id': outcome.event_id, 'error': outcome.error})
        return outcome.status in remediation.RENAME_DONE

    for c in candidates:
        old, new = c['request_number'], c['projcode']
        item = {'request_id': c['request_id'], 'request_number': old, 'projcode': new}
        if not rename(c['request_id'], old, new, item):
            continue
        # A Renewal filed before the rename is a second line under the old number.
        try:
            others = live_lines(admin.reader.get_request_family_by_number(old))
        except XrasSourceUnavailable as exc:
            ctx.logger.warning('xras_rename: could not re-read %s for siblings: %s', old, exc)
            continue
        for line in others:
            detail['siblings'] += 1
            rid = line.get('requestId')
            rename(rid, old, new, {'request_id': rid, 'request_number': old, 'projcode': new})

    done = detail['verified'] + detail['already_renamed']
    return TaskResult(detail=detail, partial_failures=detail['failed'],
                      message=f"{detail['verified']} renamed, {done} of "
                              f"{detail['candidates'] + detail['siblings']} done")
