"""Sample render contexts: the preview input and the variable palette.

``sample_context(kind)`` returns fixed literals shaped exactly like the
context the real builder passes for that kind; the builder tests pin the two
key sets against each other. The admin template editor previews against it
and lists its keys, with ``VARIABLE_NOTES``, as the variables an operator may
use. Imports only the notify vocabulary, never jinja2 or the ORM.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sam.notify.base import Recipient
from sam.notify.kinds import get_kind

#: Keys ``TemplateRenderer.render`` adds to every context from the Message.
INJECTED_KEYS = ('subject', 'recipient', 'recipient_name', 'recipient_role')

_PROJECT = {
    'project_code': 'SCSG0001',
    'project_title': 'Sample Project Title',
    'project_lead': 'Jane Lead',
    'project_lead_email': 'jane.lead@example.edu',
}

_SUBJECTS = {
    'expiration': 'NSF NCAR Project SCSG0001 Expiration Notice',
    'xras_activation': 'NSF NCAR Project SCSG0001 is now active',
    'xras_supplement': 'NSF NCAR Project SCSG0001 has received additional allocation',
    'xras_extension': 'NSF NCAR Project SCSG0001 allocation has been extended',
    'xras_update': 'NSF NCAR Project SCSG0001 allocation has been renewed',
    'xras_adjustment': 'NSF NCAR Project SCSG0001 allocation has been adjusted',
    'task_summary': '[SAM] expiration_notices: 11 sent, 1 FAILED',
}

_XRAS_ACTION_TYPES = {
    'xras_activation': 'New',
    'xras_supplement': 'Supplement',
    'xras_extension': 'Extension',
    'xras_update': 'Renewal',
    'xras_adjustment': 'Adjustment',
}


def sample_recipient(role: str = 'lead') -> Recipient:
    """The addressee a preview is rendered for."""
    names = {'lead': 'Jane Lead', 'admin': 'Alex Admin', 'user': 'Sam User',
             'operator': 'SAM operator'}
    return Recipient('preview@example.edu', name=names.get(role, 'Sam User'),
                     role=role)


def _expiration(facility: Optional[str]) -> Dict[str, Any]:
    return {
        **_PROJECT,
        'resources': [
            {'resource_name': 'Derecho', 'expiration_date': '2026-09-30',
             'days_remaining': 30, 'allocated_amount': 1_000_000.0,
             'used_amount': 650_000.0, 'remaining_amount': 350_000.0,
             'units': 'hours'},
            {'resource_name': 'Campaign Storage', 'expiration_date': '2026-09-30',
             'days_remaining': 30, 'allocated_amount': 20.0,
             'used_amount': 12.5, 'remaining_amount': 7.5, 'units': 'TiB'},
        ],
        'latest_expiration': '2026-09-30',
        'grace_expiration': '2026-12-29',
        'facility': facility or 'UNIV',
        'milestone': 'expiring',
    }


def _xras(kind: str) -> Dict[str, Any]:
    return {
        **_PROJECT,
        'resources': [
            {'resource_name': 'Casper', 'amount': '50,000', 'units': 'hours',
             'end_date': '2027-09-30'},
            {'resource_name': 'Derecho', 'amount': '1.15M', 'units': 'hours',
             'end_date': '2027-09-30'},
        ],
        'added': ([{'resource_name': 'Derecho', 'amount': '150,000',
                    'units': 'hours'}]
                  if kind == 'xras_supplement' else []),
        'changes': ([{'resource_name': 'Derecho', 'amount': '-100,000',
                      'units': 'hours'}]
                    if kind == 'xras_adjustment' else []),
        'action_type': _XRAS_ACTION_TYPES[kind],
        'approver_comment': 'Approved as recommended by the panel.',
    }


def _task_summary() -> Dict[str, Any]:
    return {
        'task_name': 'expiration_notices',
        'occurrence': '2026-09-07T15:00:00',
        'headline': '11 sent, 1 FAILED',
        'aborted': False,
        'abort_reason': None,
        'per_project': [{'projcode': 'SCSG0001', 'count': 3},
                        {'projcode': 'UCUB0001', 'count': 2}],
        'failures': [{'recipient': 'bounced@example.edu',
                      'detail': '550 5.1.1 mailbox unavailable'}],
        'window_start': '2026-09-07T00:00:00',
        'window_end': '2026-10-17T00:00:00',
        'milestones': ['expiring'],
        'projects': 5,
        'selected': 40,
        'suppressed': 28,
        'audience': 12,
        'sent': 11,
        'failed': 1,
        'failed_recipients': ['bounced@example.edu'],
    }


def sample_context(kind: str, facility: Optional[str] = None) -> Dict[str, Any]:
    """A builder-shaped context for ``kind``; raises ValueError on an unknown kind."""
    key = get_kind(kind).key
    if key == 'expiration':
        return _expiration(facility)
    if key == 'task_summary':
        return _task_summary()
    return _xras(key)


def preview_context(kind: str, facility: Optional[str] = None,
                    role: str = 'lead') -> Dict[str, Any]:
    """``sample_context`` plus the four keys the renderer injects."""
    recipient = sample_recipient(role)
    context = {
        **sample_context(kind, facility),
        'subject': _SUBJECTS[get_kind(kind).key],
        'recipient': recipient.address,
        'recipient_name': recipient.name,
        'recipient_role': recipient.role,
    }
    # Mirror build_xras_messages: the approver note is PI-only.
    if role != 'lead' and 'approver_comment' in context:
        context['approver_comment'] = None
    return context


#: One line per variable, keyed by dotted name for list-item fields.
VARIABLE_NOTES: Dict[str, str] = {
    'subject': 'The mail subject line, built by the caller.',
    'recipient': 'The email address this copy is sent to.',
    'recipient_name': 'Display name of the recipient.',
    'recipient_role': "One of 'lead', 'admin', 'user' (PI mail) or 'operator'.",
    'project_code': 'The project code, e.g. SCSG0001.',
    'project_title': 'The project title.',
    'project_lead': "The project lead's display name.",
    'project_lead_email': "The project lead's primary email, or empty if none on file.",
    'resources': 'One entry per allocated resource on the project.',
    'resources.resource_name': 'Resource name, e.g. Derecho.',
    'resources.expiration_date': 'Allocation end date, formatted, or N/A.',
    'resources.days_remaining': 'Whole days until the allocation ends.',
    'resources.allocated_amount': 'Allocated amount as a number.',
    'resources.used_amount': 'Amount charged so far as a number.',
    'resources.remaining_amount': 'Allocated minus used, as a number.',
    'resources.units': "Unit label ('hours', 'TiB') or empty for unitless grants.",
    'resources.amount': 'Allocated amount, already formatted (e.g. 1.15M).',
    'resources.end_date': 'Allocation end date, formatted, or empty.',
    'latest_expiration': 'The latest end date across the expiring allocations.',
    'grace_expiration': 'The end of the 90-day data-retention grace period.',
    'facility': "Facility name ('UNIV', 'WNA'), also selects the template variant.",
    'milestone': "The notice rung label; 'expiring' today.",
    'added': 'Supplement only: the increments this request added. Empty otherwise.',
    'added.resource_name': 'Resource name.',
    'added.amount': 'Amount added, formatted, never signed.',
    'added.units': 'Unit label or empty.',
    'changes': 'Adjustment only: the signed changes this request applied. Empty otherwise.',
    'changes.resource_name': 'Resource name.',
    'changes.amount': 'Signed change, formatted (+150,000 or -100,000).',
    'changes.units': 'Unit label or empty.',
    'action_type': 'The XRAS action type as received, or empty.',
    'approver_comment': "The XRAS approver's note, or empty when there is none.",
    'task_name': 'The scheduled task that ran.',
    'occurrence': 'The scheduled slot this run filled, ISO-8601.',
    'headline': 'One-line outcome, also used in the subject.',
    'aborted': 'True when the run sent nothing because a guard tripped.',
    'abort_reason': 'Why the run aborted, or empty.',
    'per_project': 'Recipients counted per project, sorted by code.',
    'per_project.projcode': 'Project code.',
    'per_project.count': 'Recipients addressed for that project.',
    'failures': 'One entry per delivery that failed.',
    'failures.recipient': 'The address that failed.',
    'failures.detail': "The transport's error text.",
    'window_start': 'Start of the selection window, ISO-8601.',
    'window_end': 'End of the selection window, ISO-8601.',
    'milestones': 'Rung labels the run selected for.',
    'projects': 'Distinct projects in the selection.',
    'selected': 'Allocations matching the window.',
    'suppressed': 'Messages dropped because they were already sent.',
    'audience': 'Messages the run intended to send.',
    'sent': 'Messages delivered. Absent when the run aborted.',
    'failed': 'Messages that failed. Absent when the run aborted.',
    'failed_recipients': 'Addresses that failed, capped.',
}


def _shape(value: Any) -> str:
    if isinstance(value, bool):
        return 'true/false'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, list):
        if not value:
            return 'list (empty for this kind)'
        return 'list' if isinstance(value[0], dict) else 'list of text'
    if value is None:
        return 'text or empty'
    return 'text'


def palette(kind: str, facility: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rows of ``{name, shape, example, note, parent, children}`` for the editor.

    Alphabetical at the top level; a list's item fields follow it directly,
    alphabetical too, with ``parent`` set so the table can fold them.
    """
    rows = []
    for name, value in sorted(preview_context(kind, facility).items()):
        fields = (sorted(value[0].items())
                  if isinstance(value, list) and value and isinstance(value[0], dict)
                  else [])
        rows.append({'name': name, 'shape': _shape(value),
                     'example': '' if isinstance(value, list) else value,
                     'note': VARIABLE_NOTES.get(name, ''),
                     'parent': None, 'children': len(fields)})
        for field, example in fields:
            dotted = f'{name}.{field}'
            rows.append({'name': dotted, 'shape': _shape(example),
                         'example': example,
                         'note': VARIABLE_NOTES.get(dotted, ''),
                         'parent': name, 'children': 0})
    return rows
