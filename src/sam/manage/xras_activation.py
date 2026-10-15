"""Activate a project an XRAS New created inactive: the card's Activate button
and, behind ``XRAS_AUTO_ACTIVATE``, the ``/actions`` route once a New applies
(docs/plans/XRAS_POST_AUTOMATION.md § 3 B). The route's path is the one unattended
exception to ``can_edit_project_governance``; the card checks it before calling.
"""

from __future__ import annotations

from typing import Optional

from sam.integration.xras import XrasActivationEvent
from sam.manage.transaction import management_transaction


def auto_activate_armed() -> bool:
    from sam.integration._config import config_bool
    return config_bool('XRAS_AUTO_ACTIVATE', False)


def activate_project(session, project, *, by: str,
                     xras_action_log_id: Optional[int] = None
                     ) -> Optional[XrasActivationEvent]:
    """Activate ``project`` and record who did, in one transaction. An active
    project writes nothing and returns ``None``. ``xras_action_log_id`` defaults
    to the newest action naming the project."""
    from sam.queries.xras_activation import get_latest_xras_action_id
    if project.is_active:
        return None
    if xras_action_log_id is None:
        xras_action_log_id = get_latest_xras_action_id(session, project.project_id)
    with management_transaction(session):
        # reactivate(), not update(active=True): the latter deliberately leaves
        # inactivate_time alone (see the method docstring).
        project.reactivate()
        # Inside the transaction: an event that outlived its effect would leave
        # the card showing a pending project the audit calls activated.
        return XrasActivationEvent.create(session, project_id=project.project_id,
                                          event_type='activated', created_by=by,
                                          xras_action_log_id=xras_action_log_id)


def activate_posted_project(session, projcode: str, *, by: str,
                            xras_action_log_id: Optional[int] = None) -> str:
    """The route's after-reply path: ``activated``, ``already active`` or ``not found``."""
    from sam.projects.projects import Project
    project = Project.get_by_projcode(session, projcode)
    if project is None:
        return 'not found'
    event = activate_project(session, project, by=by, xras_action_log_id=xras_action_log_id)
    return 'activated' if event is not None else 'already active'
