"""Messages for the manual Renew/Extend action's optional lead notification.

Not exported from ``sam.queries``: like ``xras_notices`` this imports
``sam.notify``, and the package namespace must not drag the mailer into every
consumer of the ORM (see ``tests/unit/gates/test_notify_import_graph.py``).

The action runs once, at the tree root, but the notice fans out: one
personalized copy per project in the tree, to that project's lead and admin.
A project with a sub-tree is told it can move allocations among its children
(the ``can_exchange_allocations`` capability); a leaf is not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional, Sequence

from sqlalchemy.orm import Session

from sam import fmt
from sam.enums import ResourceTypeName
from sam.notify.audience import to_recipients
from sam.notify.base import Message
from sam.queries.xras_activation import get_xras_pending_recipients

_SUBJECT = 'Your NSF NCAR project {projcode} has been {action}'


def renewal_dedup_key(action: str, projcode: str,
                      new_end: Optional[datetime], address: str) -> str:
    """Suppress a re-click of the same action, but not a genuinely new period.

    Keyed on the target end date and the action word, so renewing for FY28
    after FY27 mints a fresh key while re-submitting the same renew does not.
    """
    stamp = new_end.strftime('%Y-%m-%d') if new_end else 'none'
    return f'project_renewal:{action}:{projcode}:{stamp}:{address}'


def _resource_rows(allocations) -> List[dict]:
    """The renewed/extended allocations as template rows, sorted by resource."""
    rows = []
    for alloc in allocations:
        resource = alloc.account.resource if alloc.account else None
        if resource is None:
            continue
        rtype = (resource.resource_type.resource_type
                 if resource.resource_type else None)
        rows.append({
            'resource_name': resource.resource_name,
            'amount': fmt.number(alloc.amount),
            'units': ResourceTypeName.allocation_unit(rtype, alloc.amount),
            'end_date': fmt.date_str(alloc.end_date, null=None),
        })
    rows.sort(key=lambda r: r['resource_name'])
    return rows


def build_renewal_messages(session: Session, root_project, *,
                           action: str,
                           new_end: Optional[datetime],
                           touched_allocations: Sequence,
                           requested_by: str,
                           url_builder: Callable[[str], str]) -> List[Message]:
    """One Message per (project in the tree x lead/admin), personalized.

    ``action`` is ``'renewed'`` or ``'extended'``. ``touched_allocations`` are
    the allocations the action just created/updated; grouped by owning project
    they supply each project's resource table, so a future-dated FY27 renewal
    shows exactly what was written rather than what is active today.
    ``url_builder`` maps a projcode to that project's Edit-page deep link (the
    caller owns it so this stays Flask-free).
    """
    by_project: dict[int, list] = {}
    for alloc in touched_allocations:
        pid = alloc.account.project_id if alloc.account else None
        if pid is not None:
            by_project.setdefault(pid, []).append(alloc)

    projects = root_project.get_descendants(include_self=True)
    recipients_by_project = get_xras_pending_recipients(
        session, [p.project_id for p in projects])

    messages: List[Message] = []
    for project in projects:
        people = recipients_by_project.get(project.project_id, [])
        if not people:
            continue
        has_subtree = not project.is_leaf()
        context = {
            'project_code': project.projcode,
            'project_title': project.title,
            'project_lead': (project.lead.display_name
                             if project.lead else 'Project Lead'),
            'project_lead_email': (project.lead.primary_email
                                   if project.lead else None),
            'action': action,
            'has_subtree': has_subtree,
            'resources': _resource_rows(by_project.get(project.project_id, [])),
            'manage_url': url_builder(project.projcode),
        }
        subject = _SUBJECT.format(projcode=project.projcode, action=action)
        for recipient in to_recipients(people):
            messages.append(Message(
                kind='project_renewal',
                recipient=recipient,
                subject=subject,
                context=context,
                entity=('project', project.project_id),
                projcode=project.projcode,
                dedup_key=renewal_dedup_key(action, project.projcode,
                                            new_end, recipient.address),
                requested_by=requested_by,
            ))
    return messages
