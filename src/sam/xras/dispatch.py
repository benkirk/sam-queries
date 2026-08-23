"""Choosing a handler, and the two flags that can stop one running.

Dispatch is on the PAIR ``(actionType, does the project exist)``, first match
wins: New-and-not-exists -> Add; (New or Renewal)-and-exists -> Update;
Extension/Supplement/Transfer -> their own; no match -> manual fallback, 200.

Three traps the payload corpus proved, each producing a wrong dispatch:

1. **"Update" is not an ``actionType``.** It is a handler. The wire vocabulary
   is ``New, Renewal, Extension, Supplement, Transfer, Adjustment, Advance``.
2. **``New`` does not imply a new project.** A ``New`` whose ``requestNumber``
   is an existing projcode routes to Update. ONLY the database can tell them
   apart -- a request token is projcode-*shaped*, so no prefix or shape rule
   separates them.
3. **``requestType`` is useless for dispatch.** Every sampled payload carries
   ``requestType: 'New'``, including the Extensions, Supplements and the
   Adjustment. Only ``actionType`` selects, and not alone.

Both spellings of Adjust are accepted via
``sam.queries.xras_actions.canonical_action_type`` rather than a second map --
legacy compares ``"Adjust"`` against a wire sending ``"Adjustment"``, so its
handler has never fired.

``XRAS_ACTIONS_CAPTURE_ONLY`` is the INTERLOCK -- nothing dispatches while it
is on -- and is checked by the route, before this module.
``XRAS_ACTIONS_ENABLED`` is the TRIAGE lever, not a rollout mechanism: all six
handlers ship enabled, so a misbehaving payload class can be parked by config
instead of by revert. It keys on action type rather than handler because that
is the column an operator is looking at, which means disabling ``New``
disables both Add and Update -- the honest granularity.

Handlers register by importing this module and calling :func:`register`; until
one does, its service selects and falls through to ``manual``.
See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``, *The dispatcher*.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from sam.projects.projects import Project
from sam.queries.xras_actions import XRAS_ACTION_TYPES, canonical_action_type

from .wire import get_field

logger = logging.getLogger(__name__)

__all__ = [
    'DispatchResult',
    'ALL_ACTION_TYPES',
    'SERVICES',
    'register',
    'registered_services',
    'parse_enabled_action_types',
    'select_service',
    'dispatch_action',
]

#: Every canonical ``actionType`` the wire may carry. The single source is
#: ``sam.queries.xras_actions``; re-exported so callers configuring the allowlist do
#: not have to reach into the query layer for the vocabulary.
ALL_ACTION_TYPES: FrozenSet[str] = frozenset(XRAS_ACTION_TYPES)

#: Legacy's service names, in ``ActionConfig``'s registration order. The order is
#: preserved rather than reduced to a dict because first-match-wins is the documented
#: semantics, even though the selectors below happen to be disjoint once ``exists`` is
#: taken into account.
SERVICES: Tuple[str, ...] = (
    'add', 'update', 'extend', 'supplement', 'transfer', 'adjust',
)


@dataclass(frozen=True)
class DispatchResult:
    """What the route needs to close out the audit row.

    ``status`` is the ``xras_action_log`` status: ``'processed'`` when a handler wrote
    something, ``'manual'`` when nobody serviced it. The 422 path does **not** come
    back through here — it raises
    :class:`~sam.xras.errors.XrasActionRejected`, because assemble-then-check-once
    means a rejection happens before any transaction is opened.

    ``service`` is recorded even on the ``manual`` arm: knowing that an Extension
    parked because Extension was *disabled*, rather than because nothing matched, is
    the difference between a two-minute triage and a long one.
    """

    status: str
    service: Optional[str] = None
    projcode: Optional[str] = None
    reason: Optional[str] = None
    warnings: Tuple[str, ...] = field(default=())


#: ``service name -> handler``. Populated by :func:`register` at import time of each
#: handler module; empty until the first one lands.
_HANDLERS: Dict[str, Callable] = {}


def register(service: str, handler: Callable) -> None:
    """Bind a handler to one of :data:`SERVICES`.

    Raises rather than warning on an unknown name or a double registration: both are
    programming errors, and a handler that silently failed to register would route live
    traffic to the manual fallback while every test that called it directly still
    passed.
    """
    if service not in SERVICES:
        raise ValueError(f'unknown XRAS service {service!r}; expected one of {SERVICES}')
    if service in _HANDLERS:
        raise ValueError(f'XRAS service {service!r} is already registered')
    _HANDLERS[service] = handler


def registered_services() -> FrozenSet[str]:
    """Which services currently have a handler. Used by the tests that assert the
    manual arm stays reachable for the ones that do not."""
    return frozenset(_HANDLERS)


def parse_enabled_action_types(value: Optional[str]) -> FrozenSet[str]:
    """Read the ``XRAS_ACTIONS_ENABLED`` setting into a set of canonical types.

    Accepts ``'all'`` (the default), ``'none'``, or a comma-separated list of action
    types in either spelling — ``'Extension,Adjust'`` and ``'Extension,Adjustment'``
    mean the same thing.

    An unrecognised token is **logged and dropped**, which fails safe in the direction
    that matters: a typo like ``'Extention'`` leaves Extension *disabled*, so its
    actions park as ``manual`` for a human rather than being written by a handler
    nobody meant to enable. Refusing to start would be worse — this is the lever
    reached for during an incident, and it must not be able to take the app down.
    """
    if value is None:
        return frozenset(ALL_ACTION_TYPES)

    text = value.strip()
    if not text or text.lower() == 'all':
        return frozenset(ALL_ACTION_TYPES)
    if text.lower() == 'none':
        return frozenset()

    enabled = set()
    for token in text.split(','):
        token = token.strip()
        if not token:
            continue
        canonical = canonical_action_type(token)
        if canonical in ALL_ACTION_TYPES:
            enabled.add(canonical)
        else:
            logger.warning(
                'XRAS_ACTIONS_ENABLED lists an unknown action type %r — ignoring it, '
                'so that type stays DISABLED and its actions park as manual. '
                'Known types: %s', token, ', '.join(sorted(ALL_ACTION_TYPES)))
    return frozenset(enabled)


def select_service(session, action) -> Optional[str]:
    """Which legacy service would handle this action, or ``None`` for no match.

    Resolves ``requestNumber`` as a projcode against the **database** — never against
    a prefix or shape rule, for the reason in the module docstring. The lookup does not
    filter on ``active``, and must not: XRAS-created projects arrive ``active = 0`` by
    design (``InactivateNewProject``), so an active-only existence check would route a
    re-posted New action to the Add handler and mint a second project.
    """
    action_type = canonical_action_type(get_field(action, 'actionType'))
    request_number = (get_field(action, 'requestNumber') or '').strip()
    exists = bool(request_number) and Project.get_by_projcode(
        session, request_number) is not None

    if action_type == 'New' and not exists:
        return 'add'
    if action_type in ('New', 'Renewal') and exists:
        return 'update'
    if not exists:
        # Every remaining service requires an existing project. Legacy reaches the
        # same conclusion by falling off the end of the selector chain.
        return None
    if action_type == 'Extension':
        return 'extend'
    if action_type == 'Supplement':
        return 'supplement'
    if action_type == 'Transfer':
        return 'transfer'
    if action_type == 'Adjustment':
        return 'adjust'
    return None


def dispatch_action(session, action, *,
                    enabled: Optional[FrozenSet[str]] = None,
                    validate_only: bool = False) -> DispatchResult:
    """Select a handler and run it, or explain why nothing ran.

    *enabled* is passed in rather than read from config because nothing under ``sam/``
    imports Flask; ``webapp/api/xras/actions.py`` reads the setting and hands it over.
    ``None`` means everything is enabled.

    *validate_only* runs the handler's assemble-and-check half and stops, returning
    ``status='rechecked'`` — *"this would succeed if posted now"*. Nothing is written
    on that path, and a payload that would still be rejected raises exactly as it
    would live, carrying the same error list. Used by the re-check surface
    (``webapp/api/xras/recheck.py``) to answer "did our data fix take?" without
    involving XRAS. The three parking arms below are unaffected: if nothing would
    run, that is the honest answer to a re-check too.

    Raises:
        XrasActionRejected: the handler's assembly reported problems. Nothing was
            written — the contract is assemble -> check once -> execute, so this is
            raised before any transaction opens. Raised on the *validate_only* path
            for the same reason, which is what makes a re-check informative.
    """
    action_type = canonical_action_type(get_field(action, 'actionType'))
    service = select_service(session, action)

    if service is None:
        return DispatchResult(status='manual',
                              reason=f'no service matches actionType={action_type!r}')

    if enabled is not None and action_type not in enabled:
        logger.warning(
            'XRAS action type %r is disabled by XRAS_ACTIONS_ENABLED; '
            'parking action for %r as manual', action_type, service)
        return DispatchResult(
            status='manual', service=service,
            reason=f'actionType={action_type!r} is disabled by XRAS_ACTIONS_ENABLED')

    handler = _HANDLERS.get(service)
    if handler is None:
        return DispatchResult(status='manual', service=service,
                              reason=f'no handler is registered for {service!r}')

    return handler(session, action, validate_only=validate_only)
