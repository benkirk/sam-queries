"""Messages for the project *lifecycle* notices — the onboarding-rich,
tree-aware notifications an operator sends about a project's allocations.

Two entry points share one builder:

* ``build_renewal_messages`` — the optional lead notice on the Renew/Extend
  actions (#581), ``action`` in {renewed, extended}, content from the
  just-touched allocations.
* ``classify_tree_for_notice`` + ``build_lifecycle_messages`` — the manual
  "Notify" button, which classifies each project in the tree as a first-contact
  *activation* or a *adjustment* (or skips it when nothing changed since its
  last notice) and fans one personalized copy out per project × lead/admin.

Not exported from ``sam.queries``: like ``xras_notices`` this imports
``sam.notify``, and the package namespace must not drag the mailer into every
consumer of the ORM (see ``tests/unit/gates/test_notify_import_graph.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam import fmt
from sam.enums import ResourceTypeName
from sam.notify.audience import to_recipients
from sam.notify.base import Message
from sam.notify.ledger import SUPPRESSING_STATUSES
from sam.notify.models import NotificationLog
from sam.queries.xras_activation import get_xras_pending_recipients

#: The manual button's classification vocabulary; 'skip' never sends.
LIFECYCLE_ACTIONS = ('activated', 'adjusted', 'skip')

#: action word -> notification kind. Renew/Extend map to project_renewal.
_ACTION_KIND = {
    'renewed': 'project_renewal',
    'extended': 'project_renewal',
    'activated': 'project_activation',
    'adjusted': 'project_adjustment',
}


def _subject(kind: str, projcode: str, action: Optional[str]) -> str:
    if kind == 'project_renewal':
        return f'Your NSF NCAR project {projcode} has been {action}'
    if kind == 'project_activation':
        return f'Your NSF NCAR project {projcode} is now active'
    return f'Your NSF NCAR project {projcode} allocations have been updated'


def lifecycle_dedup_key(kind: str, projcode: str, action: Optional[str],
                        stamp: Optional[datetime], address: str) -> str:
    """Suppress an exact re-send, but not a genuinely new state.

    ``stamp`` is date-granularity (keeps the key within ``dedup_key``'s 128
    chars): the target end date for renewal/activation, the latest change date
    for adjustment. project_renewal keeps its #581 shape (action in the key).
    """
    s = stamp.strftime('%Y-%m-%d') if stamp else 'none'
    if kind == 'project_renewal':
        return f'project_renewal:{action}:{projcode}:{s}:{address}'
    return f'{kind}:{projcode}:{s}:{address}'


# Back-compat alias for the #581 renew/extend caller and its test.
def renewal_dedup_key(action, projcode, new_end, address):
    return lifecycle_dedup_key('project_renewal', projcode, action, new_end, address)


def _resource_rows(allocations) -> List[dict]:
    """Allocations as template rows, sorted by resource."""
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


def _context(project, action, has_subtree, allocations, manage_url) -> dict:
    return {
        'project_code': project.projcode,
        'project_title': project.title,
        'project_lead': (project.lead.display_name
                         if project.lead else 'Project Lead'),
        'project_lead_email': (project.lead.primary_email
                               if project.lead else None),
        'action': action,
        'has_subtree': has_subtree,
        'resources': _resource_rows(allocations),
        'manage_url': manage_url,
    }


def build_lifecycle_messages(session: Session, *, per_project: Sequence[dict],
                             requested_by: str,
                             url_builder: Callable[[str], str]) -> List[Message]:
    """One Message per (project × lead/admin) for the given per-project items.

    Each item is a dict with keys ``project``, ``kind``, ``action``,
    ``allocations``, ``has_subtree``, ``dedup_stamp`` (and optionally
    preloaded ``recipients``). Projects with nobody on file are dropped.
    """
    need = [it['project'].project_id for it in per_project
            if it.get('recipients') is None]
    fetched = get_xras_pending_recipients(session, need) if need else {}

    messages: List[Message] = []
    for it in per_project:
        project = it['project']
        people = it.get('recipients')
        if people is None:
            people = fetched.get(project.project_id, [])
        if not people:
            continue
        kind, action = it['kind'], it.get('action')
        context = _context(project, action, it['has_subtree'],
                           it['allocations'], url_builder(project.projcode))
        subject = _subject(kind, project.projcode, action)
        for recipient in to_recipients(people):
            messages.append(Message(
                kind=kind,
                recipient=recipient,
                subject=subject,
                context=context,
                entity=('project', project.project_id),
                projcode=project.projcode,
                dedup_key=lifecycle_dedup_key(kind, project.projcode, action,
                                              it.get('dedup_stamp'),
                                              recipient.address),
                requested_by=requested_by,
            ))
    return messages


def build_renewal_messages(session: Session, root_project, *,
                           action: str,
                           new_end: Optional[datetime],
                           touched_allocations: Sequence,
                           requested_by: str,
                           url_builder: Callable[[str], str]) -> List[Message]:
    """The Renew/Extend notice (#581): fan out over the tree from the touched
    allocations, grouped by owning project."""
    by_project: dict[int, list] = {}
    for alloc in touched_allocations:
        pid = alloc.account.project_id if alloc.account else None
        if pid is not None:
            by_project.setdefault(pid, []).append(alloc)

    per_project = [
        {'project': project, 'kind': 'project_renewal', 'action': action,
         'allocations': by_project.get(project.project_id, []),
         'has_subtree': not project.is_leaf(), 'dedup_stamp': new_end}
        for project in root_project.get_descendants(include_self=True)
    ]
    return build_lifecycle_messages(session, per_project=per_project,
                                    requested_by=requested_by,
                                    url_builder=url_builder)


# --------------------------------------------------------------------------- #
# Manual "Notify" button: classify the tree, then build from the operator's
# per-project choices.
# --------------------------------------------------------------------------- #

@dataclass
class ProjectNotice:
    """One row of the manual Notify modal's project tree."""
    project: object
    depth: int
    allocations: list
    has_subtree: bool
    recipients: list
    auto_action: str                      # 'activated' | 'adjusted' | 'skip'
    last_notified: Optional[datetime]     # last delivered lifecycle notice
    latest_change: Optional[datetime]     # newest allocation create/modify

    @property
    def period_end(self) -> Optional[datetime]:
        ends = [a.end_date for a in self.allocations if a.end_date is not None]
        return max(ends) if ends else None

    def item_for(self, action: str) -> Optional[dict]:
        """A ``build_lifecycle_messages`` item for the chosen action, or None
        when the action is 'skip'."""
        if action == 'skip':
            return None
        kind = _ACTION_KIND[action]
        stamp = self.latest_change if action == 'adjusted' else self.period_end
        return {'project': self.project, 'kind': kind, 'action': action,
                'allocations': self.allocations, 'has_subtree': self.has_subtree,
                'recipients': self.recipients, 'dedup_stamp': stamp}


def _live_allocations_at(project, active_at: datetime) -> list:
    return [a for account in project.accounts if not account.deleted
            for a in account.live_allocations if a.is_active_at(active_at)]


def _last_lifecycle_notice_times(session, projcodes) -> dict:
    """{projcode: newest delivered activation/adjustment notice time}."""
    if not projcodes:
        return {}
    when = func.coalesce(NotificationLog.sent_time, NotificationLog.creation_time)
    rows = (session.query(NotificationLog.projcode, func.max(when))
            .filter(NotificationLog.projcode.in_(projcodes),
                    NotificationLog.kind.in_(('project_activation',
                                              'project_adjustment')),
                    NotificationLog.status.in_(SUPPRESSING_STATUSES))
            .group_by(NotificationLog.projcode).all())
    return {projcode: t for projcode, t in rows}


def _build_notice(project, depth, allocs, people, last) -> ProjectNotice:
    latest_change = max(
        (t for a in allocs
         for t in (a.modified_time, a.creation_time) if t is not None),
        default=None)
    if last is None:
        auto = 'activated'
    elif latest_change is not None and latest_change > last:
        auto = 'adjusted'
    else:
        auto = 'skip'
    return ProjectNotice(
        project=project, depth=depth, allocations=allocs,
        has_subtree=not project.is_leaf(),
        recipients=people, auto_action=auto,
        last_notified=last, latest_change=latest_change)


def classify_tree_for_notice(session: Session, root_project, *,
                             active_at: datetime) -> List[ProjectNotice]:
    """One row per project (root + descendants) that has live allocations at
    ``active_at``, with a default action.

    never notified -> 'activated'; notified and an allocation changed since ->
    'adjusted'; notified and unchanged -> 'skip' (the no-noise rule). Skipped
    rows are still returned so the operator can see and re-include them.
    """
    projects = root_project.get_descendants(include_self=True)
    root_depth = root_project.get_depth()
    recipients = get_xras_pending_recipients(
        session, [p.project_id for p in projects])
    last_times = _last_lifecycle_notice_times(
        session, [p.projcode for p in projects])

    rows: List[ProjectNotice] = []
    for project in projects:
        allocs = _live_allocations_at(project, active_at)
        if not allocs:
            continue
        rows.append(_build_notice(
            project, project.get_depth() - root_depth, allocs,
            recipients.get(project.project_id, []),
            last_times.get(project.projcode)))
    return rows


def notice_for_project(session: Session, project, *,
                       active_at: datetime) -> Optional[ProjectNotice]:
    """The single-project classification (for the modal's per-row preview),
    without walking a subtree. None when the project has no live allocations."""
    allocs = _live_allocations_at(project, active_at)
    if not allocs:
        return None
    people = get_xras_pending_recipients(
        session, [project.project_id]).get(project.project_id, [])
    last = _last_lifecycle_notice_times(
        session, [project.projcode]).get(project.projcode)
    return _build_notice(project, 0, allocs, people, last)
