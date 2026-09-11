"""Real notification messages for ONE project, for the template editor's preview.

Not exported from ``sam.queries``: like ``expiration_notices`` and
``xras_notices`` this imports ``sam.notify``, which the package namespace
must not drag into every consumer.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from sam.integration.xras_api.comments import approver_comment_for_action
from sam.notify.base import Message
from sam.notify.kinds import get_family, get_kind
from sam.queries.expiration_notices import MILESTONES, build_expiration_messages
from sam.queries.xras_activation import (
    get_latest_xras_action_id, get_xras_pending_recipients,
)
from sam.queries.xras_notices import build_xras_messages, load_xras_action


def is_project_kind(kind: str) -> bool:
    """Whether a kind is about a project (so a real one can be previewed)."""
    return get_family(get_kind(kind).family).about_project


def expiring_rows_for_project(project, *,
                              now: Optional[datetime] = None) -> List[Tuple]:
    """The expiration builder's 4-tuples for every dated allocation on ``project``.

    One row per account: the allocation active now, else the latest by end
    date, the same choice ``get_detailed_allocation_usage`` makes. The window
    queries cannot serve here: they drop inactive and open-ended projects.
    """
    now = now or datetime.now()
    rows = []
    for account in project.accounts:
        if account.deleted:
            continue
        dated = [a for a in account.live_allocations if a.end_date is not None]
        if not dated:
            continue
        active = [a for a in dated if a.is_active_at(now)]
        allocation = max(active or dated, key=lambda a: a.end_date)
        rows.append((project, allocation, account.resource.resource_name,
                     (allocation.end_date - now).days))
    return rows


def messages_for_project(session: Session, kind: str, project, *,
                         requested_by: str) -> List[Message]:
    """One message per real recipient, exactly as the builders would send.

    Empty when nobody is addressable, or (expiration) when no allocation
    has an end date. Raises ``ValueError`` for a kind that is not about a
    project.
    """
    if not is_project_kind(kind):
        raise ValueError(f'{kind!r} is not a project notification')
    if get_kind(kind).family == 'expiration':
        rows = expiring_rows_for_project(project)
        if not rows:
            return []
        return build_expiration_messages(rows, milestone=MILESTONES[0],
                                         requested_by=requested_by)
    people = get_xras_pending_recipients(
        session, [project.project_id]).get(project.project_id, [])
    if not people:
        return []
    action = load_xras_action(
        session, get_latest_xras_action_id(session, project.project_id))
    return build_xras_messages(
        session, project, people, action=action, kind=kind,
        requested_by=requested_by,
        approver_comment=approver_comment_for_action(action))
