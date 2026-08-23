"""Layer-2 builders for the XRAS integration tables.

Same contract as the rest of ``tests/factories``: ``session`` first positional,
auto-build the minimum FK graph, ``flush()`` (never ``commit()``), return the
instance with its primary key populated.

These existed as sixteen hand-rolled ``session.add(...)`` calls across nine modules,
plus three local factories living inside ``test_xras_action_queries.py``. The local
ones were correctly shaped — session-first, flush, return — which is exactly why they
belonged here instead.

WARNING: **Mapping keys are derived from ``resource_id``, and that is not arbitrary.** Every
module used to pick its own magic base (``300_000``, ``900_000``, ``910_000``,
``930_000``, ``960_000``, ``980_000``, ``990_000``) purely to stay out of the others'
way, which is convention doing a job the database can do properly.

The offset is what makes it safe: ``resource_id`` is a DB-assigned primary key, so it
is unique across **every xdist worker** without coordination.

WARNING: Do **not** swap this for ``next_int``. That counter is process-local and, unlike
``next_seq``, bakes in **no** worker tag — so twelve workers would all mint
``900_001`` and collide on ``resource_repository_key``, which is itself a primary key.
Tried; it produced exactly that, intermittently.
"""

from datetime import datetime

from sam.integration.xras import (
    XrasActionLog,
    XrasActivationEvent,
    XrasOpportunityAllocationType,
    XrasRemediationEvent,
    XrasResourceRepositoryKeyResource,
)

from .projects import make_allocation_type
from .resources import make_resource

#: Offset applied to ``resource_id`` to keep synthetic keys clear of the 13 real
#: mapping rows in the snapshot, whose keys are all below 10,000.
_KEY_BASE = 900_000

#: Offset applied to ``allocation_type_id`` for synthetic opportunity ids, on the
#: same reasoning as ``_KEY_BASE``: ``opportunity_id`` is this table's primary key,
#: so a process-local counter would collide across xdist workers. Real XRAS ids are
#: six digits (530,902-533,936), which this clears.
_OPPORTUNITY_BASE = 9_000_000

__all__ = [
    'make_xras_key_mapping',
    'make_xras_opportunity_mapping',
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
    key = key if key is not None else _KEY_BASE + resource.resource_id
    session.add(XrasResourceRepositoryKeyResource(
        resource_repository_key=key, resource_id=resource.resource_id))
    session.flush()
    resource.xras_key = key
    return resource


def make_xras_action(session, *, status='received', action_type='Extension',
                     request_number='UCUB0166', http_status=200, errors=None,
                     received_time=None, source_action_id=None, projcode_result=None,
                     processed_by=None, action_id=None, service=None,
                     outcome_reason=None,
                     payload='{"actionType":"Extension"}'):
    """One ``xras_action_log`` row, built directly.

    WARNING: **Not** the route's write path. ``webapp.api.xras.actions._record`` commits on
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
        source_action_id=source_action_id,
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


def make_xras_remediation_event(session, *, operation='merge_person',
                                status='attempted', by='benkirk', when=None,
                                username=None, target_username=None,
                                request_number=None, request_id=None,
                                action_id=None, role_id=None, role_type=None,
                                xa_user=None, comment=None, before_state=None):
    """One operator write against XRAS, as SAM recorded it.

    Defaults to the ``attempted`` merge row, because that is the state the
    service writes *before* dispatch and therefore the one every test about
    surviving a failure starts from.

    ``when`` back-dates ``creation_time`` after the fact for the same reason
    :func:`make_xras_activation_event` does: ``create`` always stamps *now*,
    which is right in production and useless for testing an ordering rule.

    No FK graph to build — deliberately. Every identifier on this table belongs
    to XRAS, so a remediation row needs no project, no user, and no action log
    entry to exist.
    """
    event = XrasRemediationEvent.create(
        session, operation=operation, status=status, created_by=by,
        username=username, target_username=target_username,
        request_number=request_number, request_id=request_id,
        action_id=action_id, role_id=role_id, role_type=role_type,
        xa_user=xa_user, comment=comment, before_state=before_state,
    )
    if when is not None:
        event.creation_time = when
        session.flush()
    return event


def make_xras_opportunity_mapping(session, *, allocation_type=None,
                                  opportunity_id=None, opportunity_name=None):
    """Map an XRAS ``opportunityId`` to an allocation type, creating one if needed.

    Returns the **mapping row**, not the allocation type — the opposite of
    :func:`make_xras_key_mapping`, and for the same reason it returns a resource:
    what a caller asserts on here is the mapping itself, and the type is reachable
    through ``row.allocation_type``.

    ``allocation_type`` accepts a real snapshot row as readily as a factory-built
    one, which is what the panel-collision tests need: they must pit the *actual*
    ``UNIV USS``/``UW`` pair against each other, not two synthetic panels.
    """
    if allocation_type is None:
        allocation_type = make_allocation_type(session)
    if opportunity_id is None:
        opportunity_id = _OPPORTUNITY_BASE + allocation_type.allocation_type_id

    row = XrasOpportunityAllocationType(
        opportunity_id=opportunity_id,
        allocation_type_id=allocation_type.allocation_type_id,
        opportunity_name=opportunity_name)
    session.add(row)
    session.flush()
    return row
