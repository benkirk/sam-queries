"""Turns one XRAS action into ``Message`` objects, and nothing else.

Two consumers build the same XRAS handoff notice: the operator's **Notify**
button on the Allocations -> XRAS card, and the hourly ``xras_notices``
scheduled task. They must not disagree about the audience, the payload or —
above all — the dedup key, because a disagreement there is silently a second
copy in a PI's inbox. The key is the *only* thing that makes the two paths
safe to run side by side: whichever fires second is suppressed by the ledger,
so no locking or claiming is needed around the card.

So the builder lives here, once, and both call it. It **builds** rather than
queries, which makes the module name a slight misnomer; it is here anyway
because it sits beside :mod:`sam.queries.xras_activation`, whose row shape it
consumes, and :mod:`sam.queries.notifications`, which reads back what it
caused — exactly as :mod:`sam.queries.expiration_notices` sits beside
:mod:`sam.queries.expirations`. The one place it must **not** live is inside
``sam/notify/``: that package is transport, ledger and rendering machinery and
stays domain-free.

WARNING: **Not exported from** ``sam/queries/__init__.py``. That file imports its
submodules eagerly, so listing this one would put ``sam.notify.base`` into the
import graph of every ``from sam.queries import ...`` in the tree. Import it by
full path.

The trap is that :mod:`sam.queries.xras_activation` **is** exported, and safely
— it does not import ``sam.notify``. The two modules look alike and must be
treated differently; ``tests/unit/test_notify_import_graph.py`` is the gate.

Before this module existed the code was four private helpers inside
``webapp/dashboards/allocations/blueprint.py``, reachable only through Flask.
``src/scheduling/`` is AST-gated against importing Flask, so the move is what
makes an unattended sender possible at all. Only two kinds of coupling had to
go: ``db.session`` became the ``session`` parameter, and
``current_user.username`` became ``requested_by``.

See ``docs/plans/XRAS_AUTO_NOTICES.md`` commit 2.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from sam import fmt
from sam.enums import ResourceTypeName
from sam.integration.xras import XrasActionLog, XrasResourceRepositoryKeyResource
from sam.notify import Message, to_recipients

from .xras_activation import (
    XRAS_SERVICE_KINDS,
    get_latest_xras_action_id,
    xras_dedup_key,
)

__all__ = [
    'XRAS_KIND_SUBJECTS',
    'action_increments',
    'build_xras_messages',
    'load_xras_action',
]


#: kind -> subject template.
#: The subject lives here rather than in the Jinja file because it is also the
#: `notification_log.subject` column an operator reads back in the admin log,
#: and a subject assembled inside a template cannot be searched from SQL.
XRAS_KIND_SUBJECTS: Mapping[str, str] = {
    'xras_activation': 'NSF NCAR Project {projcode} is now active',
    'xras_supplement': 'NSF NCAR Project {projcode} has received additional allocation',
    'xras_extension': 'NSF NCAR Project {projcode} allocation has been extended',
    'xras_update': 'NSF NCAR Project {projcode} allocation has been renewed',
    # Deliberately directionless: an Adjustment can subtract, and a subject
    # line promising good news is read long before the body corrects it.
    'xras_adjustment': 'NSF NCAR Project {projcode} allocation has been adjusted',
}


def load_xras_action(session: Session,
                     action_id: Optional[int]) -> Optional[XrasActionLog]:
    """One ``xras_action_log`` row, or None. No permission logic — callers gate."""
    if action_id is None:
        return None
    return session.get(XrasActionLog, action_id)


def action_increments(session: Session, action, *,
                      signed: bool = False) -> List[Dict[str, Any]]:
    """What *this* action changed, read back off its own stored payload.

    A supplement's mail has to say how much was added, and that number exists
    nowhere else: the allocation now holds the **new total**, and
    ``allocation_transaction`` records the delta without naming the XRAS
    action. The payload is the only place the increment survives, which is one
    more reason ``raw_payload`` is stored verbatim.

    ``signed=True`` prefixes a ``+`` on positive amounts, for the Adjustment
    mail. An Adjustment is the **only** action type whose amounts can be
    negative (``AdjustmentHandler`` exists to honor them — the legacy
    factory's copy-pasted ``> 0`` gate is what kept it dark), so this is the
    one message where the reader cannot infer the direction from the action
    type and has to be shown it. ``fmt.number`` already carries the minus.

    Units are computed on the **magnitude**: ``allocation_unit`` decides
    singular/plural from the value, and -1 is one hour in either direction.

    Returns ``[{'resource_name', 'amount', 'units'}]``, or ``[]`` for anything
    unparseable — a wrong number here would be worse than an absent one, so
    every failure path yields nothing rather than a guess.
    """
    if action is None or not action.raw_payload:
        return []
    try:
        payload = json.loads(action.raw_payload)
    except (ValueError, TypeError):
        return []

    wire = payload.get('resources') or []
    keys = [w.get('resourceRepositoryKey') for w in wire
            if w.get('resourceRepositoryKey') is not None]
    if not keys:
        return []

    mapped = {
        m.resource_repository_key: m.resource
        for m in session.query(XrasResourceRepositoryKeyResource)
        .filter(XrasResourceRepositoryKeyResource
                .resource_repository_key.in_(keys)).all()
    }

    out = []
    for item in wire:
        resource = mapped.get(item.get('resourceRepositoryKey'))
        if resource is None:
            continue
        try:
            amount = float(item.get('awardedAmount'))
        except (TypeError, ValueError):
            continue
        shown = fmt.number(amount)
        if signed and amount > 0:
            shown = f'+{shown}'
        out.append({
            'resource_name': resource.resource_name,
            'amount': shown,
            'units': ResourceTypeName.allocation_unit(
                resource.resource_type.resource_type
                if resource.resource_type else None, abs(amount)),
        })
    return sorted(out, key=lambda r: r['resource_name'])


def build_xras_messages(session: Session, project,
                        people: Sequence[Mapping[str, str]], *,
                        action=None, requested_by: str) -> List[Message]:
    """Build one :class:`~sam.notify.Message` per recipient for one XRAS action.

    ``dedup_key`` embeds the action, so a Supplement mints a different key from
    the New that preceded it: each outcome can be reported once, and re-opening
    the modal about the same one cannot re-mail anybody. That is the same key
    the activity table reads back to decide whether a row says "notified", and
    the same key the scheduled task mints — which is what lets the manual and
    automatic paths coexist without double-mailing.

    ``action=None`` falls back to the newest action naming the project, which
    is what the Notify button did before it became action-aware and what a
    caller with only a project id still gets.

    ``requested_by`` is what lands in ``notification_log.requested_by``, which
    the admin card renders as "who asked". The route passes
    ``current_user.username``; the task passes ``task:xras_notices``. Required
    and undefaulted on purpose — a default here would be a plausible-looking
    lie in an audit column.
    """
    if action is None:
        action = load_xras_action(
            session, get_latest_xras_action_id(session, project.project_id))

    action_id = action.xras_action_log_id if action is not None else None
    kind = XRAS_SERVICE_KINDS.get((action.service or '') if action else '',
                                  'xras_activation')

    usage = project.get_detailed_allocation_usage()
    resources = [{
        'resource_name': name,
        'amount': fmt.number(info.get('allocated')),
        'units': ResourceTypeName.allocation_unit(info.get('resource_type'),
                                                  info.get('allocated')),
        'end_date': fmt.date_str(info.get('end_date'), null=None),
    } for name, info in sorted(usage.items())]

    lead_email = project.lead.primary_email if project.lead else None
    context = {
        'project_code': project.projcode,
        'project_title': project.title,
        'project_lead': project.lead.display_name if project.lead else 'Project Lead',
        'project_lead_email': lead_email,
        'resources': resources,
        # Only one template reads each of these, but every kind carries both —
        # a template that renders an undefined name renders nothing, silently,
        # so the cheapest guard is for the key to always exist.
        'added': (action_increments(session, action)
                  if kind == 'xras_supplement' else []),
        # Signed, and separate from `added` on purpose: `added` is a promise
        # that every number in it is an increase, which the supplement wording
        # leans on. An adjustment makes no such promise.
        'changes': (action_increments(session, action, signed=True)
                    if kind == 'xras_adjustment' else []),
        'action_type': action.action_type if action else None,
    }
    subject = XRAS_KIND_SUBJECTS.get(
        kind, XRAS_KIND_SUBJECTS['xras_activation']
    ).format(projcode=project.projcode)

    return [
        Message(
            kind=kind,
            recipient=recipient,
            subject=subject,
            context=context,
            entity=('project', project.project_id),
            projcode=project.projcode,
            dedup_key=xras_dedup_key(kind, project.projcode, action_id,
                                     recipient.address),
            requested_by=requested_by,
        )
        for recipient in to_recipients(people)
    ]
