"""Layer-2 builders for the XRAS integration tables.

Same contract as the rest of ``tests/factories``: ``session`` first positional,
auto-build the minimum FK graph, ``flush()`` (never ``commit()``), return the
instance with its primary key populated.

These existed as sixteen hand-rolled ``session.add(...)`` calls across nine modules,
plus three local factories living inside ``test_xras_action_queries.py``. The local
ones were correctly shaped — session-first, flush, return — which is exactly why they
belonged here instead.

⚠️ **Mapping keys are sequenced, not hand-picked.** Every module used to choose its
own magic base (``300_000``, ``900_000``, ``910_000``, ``930_000``, ``960_000``,
``980_000``, ``990_000``) and two had already collided. ``next_int`` is
worker-namespaced, so the namespace is disjoint across xdist workers by construction
rather than by convention.
"""

from datetime import datetime

from sam.integration.xras import (
    XrasActionLog,
    XrasActivationEvent,
    XrasResourceRepositoryKeyResource,
)

from ._seq import next_int
from .resources import make_resource

__all__ = [
    'make_xras_key_mapping',
    'make_xras_action',
    'make_xras_activation_event',
]


def make_xras_key_mapping(session, *, resource=None, key=None):
    """Map an XRAS ``resourceRepositoryKey`` to a resource, creating one if needed.

    Returns the **resource**, not the mapping row, and stamps the key onto it as
    ``resource.xras_key``. That is what every call site actually wants — the key is
    what goes on the wire and the resource is what the assertions are about — and it
    is the shape the six duplicated ``mapped_resource`` fixtures had converged on
    independently.
    """
    resource = resource if resource is not None else make_resource(session)
    key = key if key is not None else 900_000 + next_int('xraskey')
    session.add(XrasResourceRepositoryKeyResource(
        resource_repository_key=key, resource_id=resource.resource_id))
    session.flush()
    resource.xras_key = key
    return resource


def make_xras_action(session, *, status='received', action_type='Extension',
                     request_number='UCUB0166', http_status=200, errors=None,
                     received_time=None, replay_of_id=None, projcode_result=None,
                     processed_by=None, action_id=None, service=None,
                     outcome_reason=None,
                     payload='{"actionType":"Extension"}'):
    """One ``xras_action_log`` row, built directly.

    ⚠️ **Not** the route's write path. ``webapp.api.xras.actions._record`` commits on
    its own connection so the audit row survives a handler rollback, which means rows
    it writes escape the suite's per-test SAVEPOINT and must be deleted explicitly
    (see the ``action_log`` fixture in ``tests/xras_audit.py``). This builder writes
    through the test session and rolls back with everything else — correct for query
    tests, useless for anything asserting what the *route* recorded.

    ``errors`` is a list; the column is newline-joined.
    """
    row = XrasActionLog(
        received_time=received_time or datetime.now(),
        remote_actor='samuel',
        action_type=action_type,
        request_number=request_number,
        raw_payload=payload,
        status=status,
        http_status=http_status,
        error_messages='\n'.join(errors) if errors else None,
        projcode_result=projcode_result,
        processed_by=processed_by,
        replay_of_id=replay_of_id,
        action_id=action_id,
        service=service,
        outcome_reason=outcome_reason,
    )
    session.add(row)
    session.flush()
    return row


def make_xras_activation_event(session, project, event_type, *, when=None,
                               by='benkirk', comment=None, notified_to=None):
    """One operator event, optionally back-dated.

    ``when`` overrides ``creation_time`` after the fact because
    ``XrasActivationEvent.create`` always stamps *now* — right for production and
    useless for testing an ordering rule, and every state the pending card derives is
    a timestamp comparison.
    """
    event = XrasActivationEvent.create(
        session, project_id=project.project_id, event_type=event_type,
        created_by=by, comment=comment, notified_to=notified_to,
    )
    if when is not None:
        event.creation_time = when
        session.flush()
    return event
