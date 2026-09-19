"""The ``account`` notification family's message builders.

Both consumers of each kind (the admin Send button and the weekly task; the
public form and any resend) must mint the same dedup key, so each message
is built in one place. The rejection notice has one consumer, the reject
form's checkbox, and lives here for the same reason. The read side is :mod:`sam.queries.account_requests`.

WARNING: NOT exported from ``sam/queries/__init__.py``. This module imports
``sam.notify``, and that file imports its submodules eagerly, so listing it
would put ``sam.notify.base`` into every ``from sam.queries import ...``.
Import by full path; the gate is ``tests/unit/gates/test_notify_import_graph.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from sqlalchemy.orm import Session

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from sam.notify import Message, Recipient

from .account_requests import events_for, group_by_event, request_views, waiting_days

#: kind -> subject. In Python, not the template: it is also the searchable
#: ``notification_log.subject`` column.
ACCOUNT_KIND_SUBJECTS = {
    'account_queue_summary': 'NCAR HPC account requests: {total} waiting, {new} new',
    'account_verify': 'Verify your email address for your NCAR HPC account request',
    'account_rejected': 'Your NCAR HPC account request',
}


def queue_summary_context(session: Session, rows: Sequence[AccountRequest], *,
                          occurrence: datetime, queue_url: str = '') -> Dict[str, Any]:
    """The digest payload: two flat lists, event groups first then the rest."""
    events = events_for(session, rows)
    views = request_views(session, rows, resolutions={}, events=events,
                          today=occurrence.date())
    groups = group_by_event(views)

    def _row(v: Dict[str, Any]) -> Dict[str, Any]:
        r = v['row']
        note = (r.comment or r.purpose_note or '').strip().splitlines()
        return {
            'name': v['name'],
            'email': v['email'],
            'organization': r.organization or '',
            'desired_username': r.desired_username or '',
            'purpose': v['purpose'],
            'project_code': v['project_code'],
            'event_code': v['event_code'],
            'sponsor': v['sponsor'].display_name if v['sponsor'] else '',
            'waiting_days': v['waiting_days'],
            'state': v['state'],
            'assignee': r.assignee or '',
            'note': note[0] if note else '',
        }

    out_events, out_rows = [], []
    for group in groups:
        event = group['event']
        if event:
            out_events.append({
                'event_code': event.event_code,
                'event_name': event.name,
                'project_code': group['project_code'],
                'deadline': event.accounts_needed_by.isoformat(),
                'count': len(group['rows']),
            })
        out_rows.extend(_row(v) for v in group['rows'])
    by_purpose: Dict[str, int] = {}
    for r in rows:
        by_purpose[r.purpose] = by_purpose.get(r.purpose, 0) + 1
    today = occurrence.date()
    return {
        'occurrence': occurrence.isoformat(timespec='seconds'),
        'total': len(rows),
        'new_count': sum(1 for r in rows if r.requested_at is None),
        'waiting_count': sum(1 for r in rows if r.requested_at is not None),
        'oldest_days': max((waiting_days(r, today=today) for r in rows), default=0),
        'by_purpose': [{'purpose': p, 'count': by_purpose[p]} for p in sorted(by_purpose)],
        'events': out_events,
        'rows': out_rows,
        'queue_url': queue_url,
    }


def build_queue_summary(session: Session, rows: Sequence[AccountRequest], *,
                        recipient: str, occurrence: datetime, requested_by: str,
                        queue_url: str = '') -> Message:
    """One digest to the configured NUSD address, keyed on the day so the
    button and the weekly task cannot both send it."""
    context = queue_summary_context(session, rows, occurrence=occurrence,
                                    queue_url=queue_url)
    address = recipient.strip()
    return Message(
        kind='account_queue_summary',
        recipient=Recipient(address, name='NUSD', role='operator'),
        subject=ACCOUNT_KIND_SUBJECTS['account_queue_summary'].format(
            total=context['total'], new=context['new_count']),
        context=context,
        dedup_key=f'account_queue_summary:{occurrence.date().isoformat()}:{address}',
        requested_by=requested_by,
    )


def build_verify_message(row: AccountRequest, *, verify_url: str, code: str,
                         expires_hours: int, event_name: Optional[str] = None,
                         requested_by: str = CREATED_BY_SELF) -> Message:
    """The verification mail. Its context carries NOTHING the visitor typed
    -- not even a name -- so SAM cannot be used as a relay with a UCAR
    return address. The key changes with every issue, so a resend is not
    suppressed by the last one."""
    issued = (row.verify_expires_at.isoformat(timespec='seconds')
              if row.verify_expires_at else 'unissued')
    return Message(
        kind='account_verify',
        recipient=Recipient(row.email, role='user'),
        subject=ACCOUNT_KIND_SUBJECTS['account_verify'],
        context={
            'verify_url': verify_url,
            'code': code,
            'expires_hours': expires_hours,
            'event_name': event_name or '',
        },
        entity=('account_request', row.account_request_id),
        dedup_key=f'account_verify:{row.account_request_id}:{issued}',
        requested_by=requested_by,
    )


def build_rejection_message(row: AccountRequest, *, requested_by: str,
                            event_name: str = '', project_code: str = '') -> Message:
    """The notice an operator chose to send on Reject. The address is verified
    or sponsor-vouched, so naming the person and echoing the operator's reason
    is fine here (the "nothing typed" rule is the verify mail's). Keyed on the
    closure time, so a reopen and a second reject can notify again."""
    closed = (row.closed_at.isoformat(timespec='seconds') if row.closed_at else 'open')
    return Message(
        kind='account_rejected',
        recipient=Recipient(row.email, name=row.display_name, role='user'),
        subject=ACCOUNT_KIND_SUBJECTS['account_rejected'],
        context={
            'name': row.display_name,
            'reason': row.closed_reason or '',
            'event_name': event_name or '',
            'project_code': project_code or '',
        },
        entity=('account_request', row.account_request_id),
        dedup_key=f'account_rejected:{row.account_request_id}:{closed}',
        requested_by=requested_by,
    )
