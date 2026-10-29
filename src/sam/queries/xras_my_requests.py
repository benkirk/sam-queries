"""The My Requests tab's reads: the user's requests with a status, and the projects they may file for.

Two sources, one readout. ``xras_submission`` rows are what SAM filed; the sweep's request
index holds every request the user has a role on. An action reads its ``xras_action_log``
row once XRAS has posted it, else XRAS's own action status. Flask-free.
Design: ``docs/plans/XRAS_SUBMISSION.md`` § 4.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: Rows the readout shows from SAM's own record; older ones stay in the table, off the page.
SUBMISSION_LIMIT = 50


@dataclass(frozen=True)
class Readout:
    label: str
    tone: str                                   # success | info | attention | danger | muted
    when: Optional[Any] = None


def action_readout(action_status: Optional[str], log=None,
                   entered: Optional[date] = None, push_state: Optional[str] = None) -> Readout:
    """What one XRAS action is waiting on: its log row once posted, else the sweep's push state,
    else XRAS's own status. ``applied_inferred`` is the sweep finding SAM already holds it."""
    from sam.queries.xras_requests import IN_FLIGHT_ACTION_STATUSES, XRAS_REPOINTED_ON
    if log is None and push_state == 'applied_inferred':
        return Readout('Applied', 'success')
    if log is not None:
        if log.status == 'processed':
            return Readout('Applied', 'success', log.processed_time)
        if log.status in ('manual', 'failed'):
            return Readout('Being reviewed by CISL', 'attention')
        if log.status == 'received':
            return Readout('Approved, being applied', 'info')
    if action_status in IN_FLIGHT_ACTION_STATUSES:
        return Readout('In review', 'info')
    if action_status == 'Approved':
        # Before the repoint legacy SAM took the post, and this log never saw it.
        if entered is not None and entered < XRAS_REPOINTED_ON:
            return Readout('Approved', 'success')
        return Readout('Approved, being applied', 'info')
    return Readout(action_status or 'Unknown', 'muted')


def stopped_step(row) -> Optional[str]:
    """The step a stopped submission halted on, from ``last_error``."""
    if not row.last_error:
        return None
    try:
        return (json.loads(row.last_error) or {}).get('step') or 'a step'
    except (ValueError, AttributeError):
        return 'a step'


def submission_readout(row, log=None, action_status: Optional[str] = None) -> Readout:
    if row.state == 'applied':
        return Readout('Applied', 'success', row.applied_time)
    if row.state == 'aborted':
        return Readout('Discarded', 'muted', row.aborted_time)
    if row.state == 'submitted':
        return action_readout(action_status or 'Submitted', log)
    step = stopped_step(row)
    if step:
        return Readout(f'Stopped at {step}', 'danger', row.modified_time)
    return Readout('Being filed', 'info', row.modified_time)


def _latest_logs(session, action_ids: Iterable[int]) -> Dict[int, Any]:
    """The newest ``xras_action_log`` row per action id, in one query."""
    from sam.integration.xras import XrasActionLog
    wanted = sorted({int(a) for a in action_ids if a})
    if not wanted:
        return {}
    out: Dict[int, Any] = {}
    for log in (session.query(XrasActionLog)
                .filter(XrasActionLog.action_id.in_(wanted))
                .order_by(XrasActionLog.xras_action_log_id)):
        out[log.action_id] = log
    return out


def _latest_action(entries: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    actions = [a for e in entries for a in e.get('actions') or ()]
    return max(actions, key=lambda a: a.get('action_id') or 0) if actions else None


def my_requests(session, username: str,
                index_rows: Optional[Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    """Every request row for *username*: SAM's filings, then the index's lines not already shown.

    Each row: ``kind`` (filed | xras), ``request_number``, ``action_type``, ``readout``,
    ``when``, ``submission`` (the row, or ``None``). Open and stopped rows sort first.
    """
    from sam.integration.xras_submission import XrasSubmission
    from sam.queries.xras_requests import request_numbers_by_username

    filed = (session.query(XrasSubmission)
             .filter((XrasSubmission.created_by == username)
                     | (XrasSubmission.xa_user == username))
             .order_by(XrasSubmission.xras_submission_id.desc())
             .limit(SUBMISSION_LIMIT).all())
    numbers = request_numbers_by_username(index_rows or (), [username]).get(
        username.casefold(), [])
    by_number: Dict[str, List[Mapping[str, Any]]] = {}
    for entry in index_rows or ():
        if entry.get('request_number') in numbers:
            by_number.setdefault(entry['request_number'], []).append(entry)
    latest = {n: _latest_action(e) for n, e in by_number.items()}
    logs = _latest_logs(session, [r.action_id for r in filed]
                        + [a['action_id'] for a in latest.values() if a])
    statuses = {a['action_id']: a.get('action_status') for a in latest.values() if a}

    rows: List[Dict[str, Any]] = []
    shown = set()
    for sub in filed:
        readout = submission_readout(sub, logs.get(sub.action_id), statuses.get(sub.action_id))
        shown.add(sub.action_id)
        rows.append(dict(kind='filed', request_number=sub.request_number,
                         action_type=sub.action_type, readout=readout,
                         when=sub.submitted_time or sub.creation_time, submission=sub,
                         open=sub.is_active))
    for number in numbers:
        action = latest.get(number)
        if action is None or action['action_id'] in shown:
            continue
        entered = action.get('entry_date') or action.get('submit_date')
        rows.append(dict(kind='xras', request_number=number,
                         action_type=action.get('action_type'),
                         readout=action_readout(action.get('action_status'),
                                                logs.get(action['action_id']), entered,
                                                (action.get('preflight') or {}).get('push_state')),
                         when=entered, submission=None, open=False))
    rows.sort(key=lambda r: (not r['open'], -_ordinal(r['when'])))
    return rows


def _ordinal(when) -> int:
    if isinstance(when, datetime):
        return when.toordinal()
    return when.toordinal() if isinstance(when, date) else 0


def led_projects(session, user) -> List[Any]:
    """The active projects *user* leads or administers, by projcode, in one query."""
    from sqlalchemy.orm import joinedload
    from sam.projects.projects import Project
    return (session.query(Project)
            .options(joinedload(Project.allocation_type))
            .filter(Project.is_active)
            .filter((Project.project_lead_user_id == user.user_id)
                    | (Project.project_admin_user_id == user.user_id))
            .order_by(Project.projcode).all())
