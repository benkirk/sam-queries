"""The contract every XRAS handler follows, as a template method.

Assemble -> check once -> execute. Legacy assembles the whole command list
first, reporting every problem into one set, then raises ONCE with the lot;
nothing is written unless assembly was clean. That is what lets an operator fix
a request in one pass instead of five, and it is the most important behavioral
property of this package. :meth:`ActionHandler.run` expresses it once so
handler seven cannot get it subtly wrong.

WARNING: ``run()`` deliberately catches NOTHING. Two exception types cross it
and both must propagate: :class:`~sam.xras.errors.XrasActionRejected` from
``raise_if_any()`` -- the 422, raised *before* the transaction opens, which is
what makes "nothing was written" true -- and
:class:`~sam.xras.handlers.new.XrasProjectCreationFailed` from inside
``execute()``, an operational failure that ``management_transaction`` rolls back
and re-raises. A ``try`` here turns either into a silent partial write.

WARNING: ``management_transaction`` is imported HERE AND NOWHERE ELSE under
``sam.xras``; ``tests/unit/test_xras_transaction_seam.py`` scans module globals
at runtime to enforce it. Handler tests neutralize the commit by monkeypatching
that name in module globals, and per-test isolation is a SAVEPOINT that a real
COMMIT releases, leaking rows into the shared xdist database. While five modules
each held their own binding, a missed patch was SILENT -- rows exist, assertions
pass, damage appears in someone else's test run.

WARNING: ``panel_authorized`` is a plain attribute, not a lazy property, and
must be assigned during :meth:`assemble`.
:func:`~sam.xras.handlers._allocations.auth_at_panel_meeting`'s second arm reads
``project.allocation_type`` -- a column Update *writes* and flushes -- so a lazy
version first read inside :meth:`execute` would read back the type the action
had just installed. Four visible assignments beat one invisible ordering
dependency.

Subclasses get the projcode, its project, the panel-authorization flag and the
error accumulator, resolved once per action.
See ``docs/xras/incoming/implemented/XRAS_HANDLER_REFACTOR.md``.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from functools import cached_property
from typing import ClassVar, Optional, Tuple

from sam.manage.allocations import create_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project

from ..dispatch import DispatchResult
from ..errors import ActionErrors
from ..wire import get_field
from ._allocations import mark_panel_authorised
from ._plans import apply_plan

logger = logging.getLogger(__name__)

__all__ = ['ActionHandler']


class ActionHandler(ABC):
    """One XRAS action, in flight.

    Subclasses implement :meth:`assemble` (pure — resolve, validate, report, write
    nothing) and :meth:`execute` (write, inside the one transaction). Everything
    between them is :meth:`run`'s job.
    """

    #: The legacy service name this handler is registered under. One of
    #: :data:`sam.xras.dispatch.SERVICES`.
    service: ClassVar[str]

    def __init__(self, session, action):
        self.session = session
        self.action = action
        self.errors = ActionErrors()

        #: Non-fatal disagreements the action survived. Today only the legacy defect-3
        #: roster/role split; carried onto the result and logged by the route.
        self.warnings: Tuple[str, ...] = ()

        #: Whether the resolved allocation type is panel-authorized (CSL or CHAP).
        #: WARNING: Assign this in :meth:`assemble`, never later — see the module docstring.
        self.panel_authorised: bool = False

        #: Set only by a handler that *mints* a projcode rather than receiving one, so
        #: :meth:`result` can report the new code. ``request_number`` and
        #: ``projcode_result`` diverge exactly on the New path, which is why
        #: ``xras_action_log`` stores both.
        self.projcode_result: Optional[str] = None

    # ---- the template ----------------------------------------------------------

    def run(self, *, validate_only: bool = False) -> DispatchResult:
        """Assemble, check once, execute, report. The whole contract, in five lines.

        ``validate_only`` stops after the check and returns ``status='rechecked'``,
        answering *"would this action succeed if posted now?"* without applying it.
        That is a complete and faithful answer rather than an approximation, because
        :meth:`assemble` is contractually write-free and the transaction is not
        opened until after ``raise_if_any()``. A payload that would be rejected
        raises :class:`XrasActionRejected` on this path exactly as on the live one,
        carrying the same ordered error list — which is the whole point: the caller
        wants the real reasons, not a boolean.

        WARNING: **This must not call :meth:`result`.** Subclasses override it to report
        what execution produced and read state that only ``execute`` creates —
        ``ExtensionHandler.result`` dereferences ``self.extended``, which does not
        exist on this path and raises ``AttributeError``. The four common fields are
        restated here instead, deliberately, so that a subclass adding a post-commit
        report cannot break re-checking.

        ``projcode`` is the ``requestNumber`` the action carried, never
        ``projcode_result`` — that is minted *during* ``execute`` on the New path, so
        reporting it would name a project this call did not create. Assembly-time
        ``warnings`` do come along: a roster disagreement is exactly what a re-check
        should surface.
        """
        self.assemble()
        self.errors.raise_if_any()
        if validate_only:
            return DispatchResult(status='rechecked', service=self.service,
                                  projcode=self.projcode or None,
                                  warnings=tuple(self.warnings),
                                  resolved=self._resolved_summary())
        with management_transaction(self.session):
            self.execute()
        return self.result()

    def _resolved_summary(self) -> Optional[dict]:
        """What assembly resolved, for the preflight board — read defensively.

        Attributes are set by :meth:`assemble` on some handlers (allocation type,
        mnemonic) and absent on others; anything missing is simply omitted. Never
        raises — a resolved summary is a display convenience, not a verdict.
        """
        alloc = getattr(self, 'allocation_type', None)
        mnem = getattr(self, 'mnemonic', None)
        out: dict = {}
        panel = getattr(alloc, 'panel', None) if alloc is not None else None
        facility = getattr(panel, 'facility', None) if panel is not None else None
        if alloc is not None:
            out['allocation_type'] = getattr(alloc, 'allocation_type', None)
        if panel is not None:
            out['panel'] = getattr(panel, 'panel_name', None)
        if facility is not None:
            out['facility_code'] = getattr(facility, 'code', None)
        if mnem is not None:
            out['mnemonic_code'] = getattr(mnem, 'code', None)
        aoi = getattr(self, 'aoi', None)
        if aoi is not None:
            out['area_of_interest'] = getattr(aoi, 'area_of_interest', None)
        # The New path mints a projcode in this series; show it before it burns one.
        if out.get('facility_code') and out.get('mnemonic_code'):
            out['series'] = f"{out['facility_code']}{out['mnemonic_code']}"
        out['panel_authorised'] = bool(getattr(self, 'panel_authorised', False))
        return out or None

    @abstractmethod
    def assemble(self) -> None:
        """Resolve and validate everything, reporting into :attr:`errors`.

        Must not write. Must not raise for a payload problem — report it and carry on,
        so the operator sees every problem at once rather than the first one.
        """

    @abstractmethod
    def execute(self) -> None:
        """Apply what :meth:`assemble` planned. Runs inside the one transaction.

        By the time this is called, ``raise_if_any()`` has passed — so values that
        assembly guaranteed non-``None`` may be used without re-checking. That
        invariant now spans two methods instead of one function body; say so where you
        rely on it.
        """

    # ---- what assembly and execution both need ---------------------------------

    def get(self, key: str):
        """Read one field off the action. See :func:`sam.xras.wire.get_field`."""
        return get_field(self.action, key)

    @cached_property
    def projcode(self) -> str:
        """``requestNumber``, stripped.

        WARNING: This is the token XRAS *sent*, which is projcode-shaped but is not
        necessarily a projcode: on the New path no such project exists, and the real
        code is minted during execution. See :attr:`projcode_result`.
        """
        return (self.get('requestNumber') or '').strip()

    @cached_property
    def project(self) -> Optional[Project]:
        """The **existing** project named by :attr:`projcode`, or ``None``.

        WARNING: Always ``None`` for the New handler, by dispatch invariant — ``select_service``
        routes to ``add`` only when no such project exists. A handler that creates a
        project must keep it under a different name; repointing this one would silently
        change what :func:`auth_at_panel_meeting`'s second arm reads.

        WARNING: Returns ``None`` rather than raising when the row is absent. Supplement and
        Adjustment both rely on that: their planners report nothing for a missing
        project, so the action completes as a ``processed`` no-op. Legacy does the same,
        and the dispatcher has already checked existence, so the arm is unreachable in
        practice — but making it raise would convert an unreachable no-op into a 500.
        """
        if not self.projcode:
            return None
        return Project.get_by_projcode(self.session, self.projcode)

    def mark_panel_authorised(self, allocation) -> None:
        """Set ``auth_at_panel_mtg`` on the CREATE row just written for *allocation*."""
        mark_panel_authorised(self.session, allocation)

    def create_allocation_for(self, project, resource, *, amount: float,
                              start: datetime, end: Optional[datetime],
                              comment: Optional[str], panel_authorised: bool):
        """``create_allocation`` plus the panel mark — the pair four handlers hand-rolled.

        *project* is explicit rather than taken from :attr:`project`, because the New
        handler passes the project it has just created inside the transaction.

        *panel_authorized* is explicit rather than read from :attr:`panel_authorized`
        for a sharper reason: Update decides it **per resource** (only the ADD branch
        marks), so an implicit read would mark rows the plan said not to.

        ``user_id=None`` is passed positionally into a parameter with no default, and
        stays that way: it is the integration-actor convention
        (``allocation_transaction.user_id IS NULL``, 25,048 production rows), not an
        oversight, and giving it a default would let the next caller inherit it by
        accident.
        """
        created = create_allocation(
            self.session,
            project_id=project.project_id,
            resource_id=resource.resource_id,
            amount=amount,
            start_date=start,
            end_date=end,
            user_id=None,
            comment=comment,
        )
        if panel_authorised:
            self.mark_panel_authorised(created)
        return created

    def execute_plan(self, steps, *, project=None) -> None:
        """Apply an ordered list of :mod:`~sam.xras.handlers._plans` records.

        WARNING: **Order is preserved and is part of the contract.** Update emits up to
        three steps for one resource and legacy applies them in emission order; this
        iterates rather than grouping by kind for that reason.

        *project* defaults to :attr:`project`, which is right for Supplement, Adjust
        and Update. New passes the project it created inside the transaction.
        """
        target = self.project if project is None else project
        for step in steps:
            apply_plan(self, step, project=target)

    # ---- the answer ------------------------------------------------------------

    def result(self, **overrides) -> DispatchResult:
        """The ``processed`` result, carrying service, projcode and warnings.

        Override to add a post-commit step — ``**overrides`` exists so that doing so
        does not mean restating the four common fields.
        """
        fields = dict(status='processed',
                      service=self.service,
                      projcode=self.projcode_result or self.projcode,
                      warnings=tuple(self.warnings))
        fields.update(overrides)
        return DispatchResult(**fields)
