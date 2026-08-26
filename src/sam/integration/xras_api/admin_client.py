"""Write client for the XRAS Allocations API -- the deliberate sibling of ``client.py``.

:class:`~sam.integration.xras_api.client.XrasApiClient` is GET-only by
construction and a test pins that no write verb exists on it. That pin is worth
keeping, so this is a SIBLING -- never a subclass, never a relaxation. They
share a config object and nothing else.

They also cannot be merged, because they live in different XRAS contexts. The
read client hardcodes ``XA-CONTEXT: report``; every write here needs ``submit``,
and the Reports family 401s under ``submit``. So verification reads that need a
roster or an action state are delegated to a read client
(:attr:`XrasAdminClient.reader`). ``GET /v1/people/<u>`` is the one route
answering under both, which is what lets merge verify itself on this connection.

Three facts from the 2026-08-21 production probe shape this module and are not
obvious from the published docs (``docs/xras/outgoing/XRAS_WRITE_PROBES.md``):

1. **One authorization rule covers every request-scoped write**: ``XA-USER``
   must hold a role on THAT request, else 401. ``arcguest`` is never
   sufficient, so request ops take an explicit *xa_user* and refuse to guess;
   person ops (merge) take none.
2. **``roleType`` is encoded differently by the two role families.** The route
   used here takes the STRING (``User``) and 400s on the numeric id.
   :data:`ROLE_TYPES` carries all three representations so a caller cannot pick
   the wrong one silently.
3. **A 200 proves only that the call was allowed.** ``POST .../submit`` returns
   a ``null`` body where the docs promise the request object, and
   ``POST /v1/people`` returns 200 while ignoring the parameter it was given.
   So every write here VERIFIES BY RE-READING, and the verdict travels in
   :class:`XrasWriteResult` rather than collapsing into an exception.

WARNING: reads retry; **writes get exactly one attempt**. A retried merge could
delete a second person and a retried submit could double-fire XRAS's review
workflow. When an outcome is ambiguous the answer is the verifying read, which
runs regardless. Only a definite 4xx short-circuits it, because nothing
happened to verify.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

import requests

from sam.integration.xras_api.base import (
    XrasSourceUnavailable,
    XrasWriteNotConfigured,
    XrasWriteRejected,
)
from sam.integration.xras_api.client import XrasApiClient, _XrasTransport, _unwrap
from sam.integration.xras_api.config import XrasApiConfig

logger = logging.getLogger(__name__)

#: Mirror of the read client's ``XA_CONTEXT``. Not a knob — see the module
#: docstring for why one class cannot serve both contexts.
XA_ADMIN_CONTEXT = 'submit'


# The role-type vocabulary (``RoleType``, ``ROLE_TYPES``, ``role_type``,
# ``PI_ROLE_TYPE_ID``) now lives in the dependency-light ``vocabulary`` module
# so the read path can import the one authoritative table without pulling in
# this write client. Re-exported here because the package ``__init__`` and
# several callers import these names from ``admin_client``.
from sam.integration.xras_api.vocabulary import (  # noqa: E402
    PI_ROLE_TYPE_ID,
    ROLE_TYPES,
    RoleType,
    role_type,
)


#: XA-CONTEXT -> the resource/allocation-date **stage** that context writes.
#: Phase 0 (2026-08-22) measured that ``submit`` touches ONLY the ``Requested``
#: stage; an ``admin``/``review`` key would reach ``Approved``/``Recommended``.
#: The verify-by-reread compares back against the stage the write targeted.
_CONTEXT_STAGE = {'submit': 'Requested', 'admin': 'Approved',
                  'review': 'Recommended'}


def _amount_str(amount: Any) -> str:
    """A plain numeric string XRAS accepts — ``'556'``, ``'556.5'``.

    XRAS stores amounts as strings (``'555.0'``); it wants a plain decimal
    string on the wire, never scientific notation (``Decimal.normalize()`` can
    yield ``'5.5E+1'`` for ``55``, which ``format(…, 'f')`` renders as ``'55'``).
    """
    from decimal import Decimal, InvalidOperation
    try:
        return format(Decimal(str(amount)).normalize(), 'f')
    except (InvalidOperation, ValueError):
        return str(amount)


def _amount_eq(a: Any, b: Any) -> bool:
    """Numeric equality that survives ``'556'`` vs ``'556.0'`` vs ``556``."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _date_str(value: Any) -> Optional[str]:
    """A ``YYYY-MM-DD`` string for the wire, or ``None``.

    Accepts a ``date``/``datetime`` (uses ``isoformat``) or a string (first ten
    characters — XRAS returns both bare dates and ``…T00:00:00Z`` timestamps).
    """
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()[:10]
    return str(value)[:10]


@dataclass(frozen=True)
class XrasWriteResult:
    """The whole record of one write attempt — what the audit row is built from.

    ``verified`` is three-valued on purpose:

    * ``True``  — a re-read confirms the intended effect,
    * ``False`` — a re-read says it did **not** happen (the isReconciled case:
      200, and nothing changed),
    * ``None``  — the verifying read itself could not be made, so the outcome
      is genuinely unknown and an operator must look.

    Collapsing ``False`` and ``None`` would tell an operator "it failed" when
    the truth is "we don't know", which for an irreversible merge is the worst
    thing this module could say.
    """

    operation: str
    method: str
    path: str
    xa_user: Optional[str] = None
    http_status: int = 0
    message: Optional[str] = None
    before: Any = None
    after: Any = None
    verified: Optional[bool] = None
    verify_detail: Optional[str] = None
    write_error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Did the write both go through *and* prove itself?"""
        return self.verified is True

    @property
    def status(self) -> str:
        """The audit vocabulary — see ``xras_remediation_event.status``."""
        if self.verified is True:
            return 'verified'
        if self.verified is False:
            return 'error' if self.write_error else 'unverified'
        return 'unverified'


class XrasAdminClient(_XrasTransport):
    """Single-attempt, self-verifying writes against the XRAS admin surface.

    Inherits the shared transport (:class:`~sam.integration.xras_api.client._XrasTransport`):
    the session, the URL builder, the per-call :meth:`_headers`, and the
    idempotent retrying :meth:`_get` used for the ``submit``-context
    verification reads. It adds the single-attempt :meth:`_write` and the write
    verbs. Its ``XA-CONTEXT`` is ``submit`` and a 4xx on any call raises
    :class:`XrasWriteRejected` (the :meth:`_client_error` override below).
    """

    #: Mirror of the read client's context knob — see the module docstring for
    #: why one class cannot serve both contexts. Not a knob.
    _CONTEXT = XA_ADMIN_CONTEXT

    def __init__(self, config: Optional[XrasApiConfig] = None,
                 reader: Optional[XrasApiClient] = None) -> None:
        super().__init__(config)
        #: Report-context reads used for verification. A separate object
        #: because the Reports family 401s under ``submit`` — see the module
        #: docstring. PRIVILEGE(#2): a key that could read
        #: ``GET /v1/requests/<rid>`` would delete this whole delegate.
        self.reader = reader or XrasApiClient(self.config)

    @classmethod
    def from_environment(cls, config: Optional[XrasApiConfig] = None,
                         reader: Optional[XrasApiClient] = None
                         ) -> 'XrasAdminClient':
        """Build a write-configured client, or refuse.

        Raises:
            XrasWriteNotConfigured: ``XRAS_WRITE_ENABLED`` is off, or the read
                lever or key is missing. A subclass of the read-side
                not-configured error, so callers that degrade on "could not
                ask" are already correct.
        """
        resolved = config or XrasApiConfig.from_environment()
        if not resolved.write_configured:
            raise XrasWriteNotConfigured(
                'XRAS writes are not configured (needs XRAS_WRITE_ENABLED=1, '
                'XRAS_OUTGOING_ENABLED=1 and XRAS_API_KEY)')
        return cls(resolved, reader=reader)

    # internals
    #
    # The session, ``_url``, ``_headers`` and the idempotent retrying ``_get``
    # are inherited unchanged from ``_XrasTransport``. ``_get`` here is the
    # submit-context verification read (the Reports family answers under
    # ``report``, so those go via ``self.reader`` instead); the single-attempt
    # rule below in ``_write`` is what distinguishes this client's *writes*.

    def _client_error(self, url: str, status: int,
                      response: 'requests.Response') -> None:
        """A 4xx on any call here is a deterministic write refusal.

        Overrides the base (which raises :class:`XrasSourceUnavailable`) so a
        ``submit``-context GET refusal carries the status the way ``_write``'s
        does — ``401`` (no role on the request), ``404`` (target did not
        resolve). ``errors[]`` parsing stays in :meth:`_write`, the only place
        a 4xx body carries a validation list.
        """
        raise XrasWriteRejected(
            f'{url} returned HTTP {status}: {response.text[:200]}',
            status=status)

    def _write(self, method: str, path: str, *,
               params: Optional[Mapping[str, Any]] = None,
               xa_user: Optional[str] = None,
               context: Optional[str] = None
               ) -> Tuple[int, Any, Optional[str], Optional[str]]:
        """**One** attempt. Returns ``(status, result, message, error)``.

        Raises:
            XrasWriteRejected: XRAS refused deterministically (4xx). Nothing
                happened, so there is nothing to verify and short-circuiting
                is safe. ``401`` means *XA-USER* holds no role on the request;
                ``400`` carries XRAS's own ``errors[]``.

        A 5xx or a dead socket does **not** raise: the write may or may not
        have applied, and only the verifying read can say which. The error text
        comes back so it can be recorded alongside the verdict.
        """
        url = self._url(path)
        try:
            response = self.session.request(
                method, url, params=params,
                headers=self._headers(xa_user, context),
                timeout=self.config.timeout)
        except requests.RequestException as exc:
            logger.warning('xras admin %s %s: transport error: %s',
                           method, url, exc)
            return 0, None, None, str(exc)

        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        message = body.get('message') if isinstance(body, dict) else None
        result = _unwrap(body) if body is not None else None

        if 400 <= status < 500:
            errors = result.get('errors') if isinstance(result, dict) else None
            logger.warning('xras admin %s %s -> %s (%s)',
                           method, url, status, message)
            raise XrasWriteRejected(
                message or f'{url} returned HTTP {status}: '
                           f'{response.text[:200]}',
                status=status, errors=errors)

        if status >= 500:
            logger.warning('xras admin %s %s -> %s', method, url, status)
            return status, result, message, f'HTTP {status}'

        logger.info('xras admin %s %s -> %s', method, url, status)
        return status, result, message, None

    # verification helpers

    def _actions(self, request_number: str) -> List[Dict[str, Any]]:
        """Every action across the **reports** family (report context).

        ``actionId`` is globally unique, so a by-id lookup needs no line
        selection -- and a verify must not miss an action on a sibling line.
        """
        family = self.reader.get_request_family_by_number(request_number) or []
        return [a for line in family if isinstance(line, dict)
                for a in (line.get('actions') or []) if isinstance(a, dict)]

    def _line(self, request_number: str,
              request_id: Optional[int] = None) -> Dict[str, Any]:
        """The line a write targeted (by ``request_id``), else the primary line.

        One family read. A ``request_id`` the family no longer carries falls
        back to the primary line -- ``delete_request`` is the one verify where
        absence is the answer, and it reads the line directly.
        """
        from sam.queries.xras_requests import line_by_id, primary_line
        family = self.reader.get_request_family_by_number(request_number) or []
        line = line_by_id(family, request_id) if request_id is not None else None
        return line or primary_line(family) or {}

    def action_status(self, request_number: str, action_id: int) -> Optional[str]:
        """The live ``actionStatus`` for one action, or ``None`` if not found."""
        for action in self._actions(request_number):
            if action.get('actionId') == action_id:
                return action.get('actionStatus')
        return None

    def roster(self, request_number: str,
               request_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """The roster of one line (the write's, by *request_id*), one row per *role*.

        PRIVILEGE(#4). WARNING: The reports payload **nests**: each ``roles[]`` entry carries a
        ``person`` plus its own ``roles[]`` list of
        ``{roleId, role, roleTypeId, …}``. Reading ``roleType`` off the outer
        object returns ``None``, which is a trap worth flattening once here.
        """
        payload = self._line(request_number, request_id)
        flat: List[Dict[str, Any]] = []
        for entry in (payload.get('roles') or []):
            if not isinstance(entry, dict):
                continue
            person = entry.get('person') or {}
            for role in (entry.get('roles') or []):
                if not isinstance(role, dict):
                    continue
                flat.append({
                    'role_id': role.get('roleId'),
                    'role_type_id': role.get('roleTypeId'),
                    'role_type': role.get('role'),
                    'username': person.get('username'),
                    'is_reconciled': person.get('isReconciled'),
                })
        return flat

    def resolve_pi(self, request_number: str,
                   request_id: Optional[int] = None) -> Optional[str]:
        """The username of the request's PI — the default impersonation target."""
        for row in self.roster(request_number, request_id):
            if row.get('role_type_id') == PI_ROLE_TYPE_ID:
                return row.get('username')
        return None

    # people

    def get_person(self, username: str) -> Optional[Dict[str, Any]]:
        """One person, read under ``submit``. Probe P0 — this route answers here."""
        result = self._get(f'/v1/people/{quote(str(username), safe="")}')
        return result if isinstance(result, dict) else None

    def merge_person(self, source: str, target: str) -> XrasWriteResult:
        """Merge *source* into *target*. **Destructive and effectively one-way.**

        XRAS deletes *source* and folds its roles into *target*. It does not
        copy person detail — ``residenceCountry`` in particular, which the
        inbound wire never carries — so both detail sheets are captured
        *before* the call and travel in the result for the audit row.

        The caller must have established that *target* resolves; this method
        fails closed on that too, because the API will happily
        *"merge a username into an existing/new username"* and a typo would
        mint a fresh identity holding the placeholder's roles.

        Verified by: *source* must stop resolving **and** *target* must still
        resolve. Either half alone is satisfiable by a no-op.
        """
        # Casefolded: XRAS matches usernames case-insensitively, so a
        # case-variant of the source is the same identity and this would be a
        # self-merge with unknown effect. The route checks this too; both,
        # because this client is also reachable from a shell.
        if str(source).strip().casefold() == str(target).strip().casefold():
            raise XrasWriteRejected(
                f'merge source and target are the same identity ({source!r}); '
                'refusing a self-merge', status=400)

        path = f'/v1/people/{quote(source, safe="")}/merge/{quote(target, safe="")}'
        # Both sheets are captured before the call: merge does not copy person
        # detail, and `residenceCountry` in particular exists nowhere else SAM
        # can reach. Source first — the dict is built in order, and the tests
        # script the transport in that order.
        before = {'source': self.get_person(source),
                  'target': self.get_person(target)}
        if before['target'] is None:
            raise XrasWriteRejected(
                f'merge target {target!r} does not resolve in XRAS; refusing '
                'to merge, which would create it', status=404)
        if before['source'] is None:
            raise XrasWriteRejected(
                f'merge source {source!r} does not resolve in XRAS '
                '(already merged away?)', status=404)

        status, _, message, error = self._write('POST', path)

        try:
            after = {'source': self.get_person(source),
                     'target': self.get_person(target)}
            verified = after['source'] is None and after['target'] is not None
            detail = ('source no longer resolves; target retained' if verified
                      else f"source resolves={after['source'] is not None}, "
                           f"target resolves={after['target'] is not None}")
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='merge_person', method='POST', path=path,
            http_status=status, message=message, before=before, after=after,
            verified=verified, verify_detail=detail, write_error=error,
            extra={'source': source, 'target': target})

    # actions

    def validate_action(self, request_id: int, action_id: int, *,
                        xa_user: str) -> Dict[str, Any]:
        """Preflight one action. ``{'validation': ..., 'errors': [...]}``.

        PRIVILEGE(#6). WARNING: **The verdict is a function of *xa_user*, not only of
        the action.**
        Probe P2 measured the same action validating successfully as the PI and
        failing as the Allocation Manager (*"The Project Lead specified for
        this request is not allowed to submit…"*). So a failure here is not
        necessarily terminal, a result must never be cached across users, and
        anything rendered from it has to name the user it was evaluated as.
        """
        result = self._get(
            f'/v1/requests/{int(request_id)}/actions/{int(action_id)}/validate',
            xa_user=xa_user)
        return result if isinstance(result, dict) else {'validation': None,
                                                        'errors': []}

    def withdraw_action(self, request_id: int, action_id: int, *,
                        request_number: str, xa_user: str) -> XrasWriteResult:
        """Un-submit one action back to ``Incomplete``.

        This is **de-approval, not deletion**: it reverts the action to a draft
        and rewrites the XRAS record so the history no longer shows an
        approval. It is reversible by :meth:`submit_action`.

        A single-action request follows its action to ``Incomplete``, which is
        what drops it out of ``reports/requests?status=Approved``; a request
        with a surviving approved sibling stays ``Approved``.

        PRIVILEGE(#3): *request_number* is required only because the write
        route is keyed on ``requestId`` while the one readable state source
        (the reports family) is keyed on the number. It exists to bridge two
        identifier spaces and would leave these signatures entirely.
        """
        path = f'/v1/requests/{int(request_id)}/actions/{int(action_id)}/submit'
        return self._act(
            'withdraw_action', 'DELETE', path,
            request_number=request_number, action_id=action_id, xa_user=xa_user,
            expect=lambda state: state == 'Incomplete',
            expectation='Incomplete')

    def submit_action(self, request_id: int, action_id: int, *,
                      request_number: str, xa_user: str,
                      preflight: bool = True) -> XrasWriteResult:
        """(Re-)submit one action. Lands in ``Under Review``, not ``Submitted``.

        With *preflight* (the default) the action is validated first as the
        same *xa_user* and a failure raises :class:`XrasWriteRejected` carrying
        XRAS's own ``errors[]``, so the modal can render them without a second
        round trip. Pass ``preflight=False`` only to let an operator override a
        preflight they have read and judged wrong — see the caveat on
        :meth:`validate_action`.
        """
        if preflight:
            check = self.validate_action(request_id, action_id, xa_user=xa_user)
            if str(check.get('validation', '')).casefold() != 'successful':
                raise XrasWriteRejected(
                    f'XRAS validation failed for action {action_id} as '
                    f'{xa_user}', status=400, errors=check.get('errors'))

        path = f'/v1/requests/{int(request_id)}/actions/{int(action_id)}/submit'
        return self._act(
            'submit_action', 'POST', path,
            request_number=request_number, action_id=action_id, xa_user=xa_user,
            expect=lambda state: bool(state) and state != 'Incomplete',
            expectation='no longer Incomplete')

    def _act(self, operation: str, method: str, path: str, *,
             request_number: str, action_id: int, xa_user: str,
             expect, expectation: str) -> XrasWriteResult:
        """Shared body for the two action verbs: capture, write once, re-read.

        WARNING: The 200 from ``.../submit`` carries a ``null`` result where the API
        docs promise the request object, so the state after a write is read
        back rather than parsed out of the response. That is the same rule the
        ``isReconciled`` finding forced, arrived at independently.
        """
        # Best-effort: the before-state is audit context, not a precondition.
        # Refusing to withdraw a stuck action because the *reports* endpoint is
        # having a bad minute would deny the operator the one control that
        # fixes it, and the after-read is what actually settles the outcome.
        # (Merge is the opposite case — there the pre-read is load-bearing, so
        # it is allowed to abort.)
        try:
            before = self.action_status(request_number, action_id)
            before_note = ''
        except XrasSourceUnavailable as exc:
            before, before_note = None, f'before-state unreadable ({exc}); '

        status, _, message, error = self._write(method, path, xa_user=xa_user)

        try:
            after = self.action_status(request_number, action_id)
            verified = expect(after)
            detail = (f'{before_note}actionStatus {before!r} -> {after!r} '
                      f'(wanted {expectation})')
        except XrasSourceUnavailable as exc:
            after, verified = None, None
            detail = f'{before_note}verify read failed: {exc}'

        return XrasWriteResult(
            operation=operation, method=method, path=path, xa_user=xa_user,
            http_status=status, message=message, before=before, after=after,
            verified=verified, verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id})

    # roles

    def add_role(self, request_id: int, role, username: str, *,
                 request_number: str, xa_user: str) -> XrasWriteResult:
        """Put *username* on the request in *role*. Returns the new ``roleId``.

        WARNING: **No person parameters are sent, ever.** This route accepts an
        optional ``firstName … isReconciled`` set that XRAS uses *to create the
        person* when the username is unknown — and ``isReconciled`` there
        defaults to **true**, which is precisely the bug that mints
        already-reconciled placeholders nobody ever merges. Omitting the
        parameters means an unknown username is XRAS's problem, not a new
        broken identity. Callers should confirm the person resolves first.

        The role travels as its **name** (``User``), not its id — this family
        400s on the numeric form. :func:`role_type` normalizes either input.
        """
        chosen = role_type(role)
        path = (f'/v1/requests/{int(request_id)}/roles/'
                f'{quote(chosen.name, safe="")}/{quote(str(username), safe="")}')
        before = self.roster(request_number, request_id)
        status, result, message, error = self._write('POST', path, xa_user=xa_user)
        role_id = result.get('roleId') if isinstance(result, dict) else None

        try:
            after = self.roster(request_number, request_id)
            wanted = str(username).casefold()
            verified = any(str(r.get('username') or '').casefold() == wanted
                           and r.get('role_type_id') == chosen.type_id
                           for r in after)
            detail = (f'{username} present as {chosen.name}' if verified
                      else f'{username} not on the roster as {chosen.name}')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='add_role', method='POST', path=path, xa_user=xa_user,
            http_status=status, message=message, before=before, after=after,
            verified=verified, verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'username': username,
                   'role_type_id': chosen.type_id, 'role_type': chosen.name,
                   'role_id': role_id})

    def remove_role(self, request_id: int, role_id: int, *,
                    request_number: str, xa_user: str) -> XrasWriteResult:
        """Take one role off the request.

        PRIVILEGE(#4). Keyed on **roleId**, not username — a person may hold more than one
        role on a request, and this route removes exactly the one named. The
        roleId comes from the roster (:meth:`roster`), which is the same place
        the sweep's index entry gets it.
        """
        path = f'/v1/requests/{int(request_id)}/roles/{int(role_id)}'
        before = self.roster(request_number, request_id)
        status, _, message, error = self._write('DELETE', path, xa_user=xa_user)

        try:
            after = self.roster(request_number, request_id)
            verified = not any(r.get('role_id') == int(role_id) for r in after)
            detail = (f'roleId {role_id} gone' if verified
                      else f'roleId {role_id} still on the roster')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='remove_role', method='DELETE', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'role_id': int(role_id)})

    # resources & allocation dates (the request editor)
    #
    # All keyed on the resource **type** id and the **stage** (Phase 0: there is
    # no per-line id in the reports feed, and at most one line per resource per
    # stage, so ``(action, resourceId, stage)`` is unambiguous). Every write is
    # query-params + verify-by-reread, exactly like the verbs above. The stage a
    # write lands in is a function of the XA-CONTEXT: ``submit`` -> Requested
    # (default), ``admin`` -> Approved. On our current key only ``submit`` is
    # authorized; the ``context=`` argument is what an elevated key flips.

    @staticmethod
    def _stage_for(context: Optional[str]) -> str:
        return _CONTEXT_STAGE.get(context or XA_ADMIN_CONTEXT, 'Requested')

    def action_resources(self, request_number: str,
                         action_id: int) -> List[Dict[str, Any]]:
        """One action's ``resources[]`` (all stages), via the reports family."""
        for action in self._actions(request_number):
            if action.get('actionId') == action_id:
                return [r for r in (action.get('resources') or [])
                        if isinstance(r, dict)]
        return []

    def action_dates(self, request_number: str,
                     action_id: int) -> List[Dict[str, Any]]:
        """One action's ``allocationDates[]``, via the reports family."""
        for action in self._actions(request_number):
            if action.get('actionId') == action_id:
                return [d for d in (action.get('allocationDates') or [])
                        if isinstance(d, dict)]
        return []

    @staticmethod
    def _amount_in(resources: List[Dict[str, Any]], resource_id: int,
                   stage: str) -> Optional[Any]:
        for res in resources:
            if res.get('resourceId') == int(resource_id) \
                    and res.get('type') == stage:
                return res.get('amount')
        return None

    def update_resource_amount(self, request_id: int, action_id: int,
                               resource_id: int, amount: Any, *,
                               request_number: str, xa_user: str,
                               comments: Optional[str] = None,
                               context: Optional[str] = None) -> XrasWriteResult:
        """Set a resource's amount for the context's stage — add-or-update.

        The same ``PUT`` both edits an existing stage line and, when none
        exists, creates one (Phase 0: on an Approved request with only a
        Recommended/Approved line, editing "the amount" as PI mints a
        **Requested** line beside the untouched award). ``comments=''`` clears
        the resource comment back to null.

        Verified by re-reading the stage's amount for this resource and
        comparing numerically.
        """
        stage = self._stage_for(context)
        amt = _amount_str(amount)
        params = {'amount': amt,
                  'comments': '' if comments is None else str(comments)}
        path = (f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
                f'/resources/{int(resource_id)}')
        before = self.action_resources(request_number, action_id)
        status, _, message, error = self._write(
            'PUT', path, params=params, xa_user=xa_user, context=context)

        try:
            after = self.action_resources(request_number, action_id)
            got = self._amount_in(after, resource_id, stage)
            verified = got is not None and _amount_eq(got, amt)
            detail = (f'{stage} amount for resource {resource_id}: '
                      f'{got!r} (wanted {amt})')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='update_resource_amount', method='PUT', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'resource_id': int(resource_id), 'amount': amt,
                   'stage': stage, 'context': context or XA_ADMIN_CONTEXT})

    def remove_resource(self, request_id: int, action_id: int,
                        resource_id: int, *, request_number: str, xa_user: str,
                        context: Optional[str] = None) -> XrasWriteResult:
        """Delete the context-stage line for a resource (Requested-only on our key).

        Verified by re-reading and confirming no line for this resource remains
        at the target stage.
        """
        stage = self._stage_for(context)
        path = (f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
                f'/resources/{int(resource_id)}')
        before = self.action_resources(request_number, action_id)
        status, _, message, error = self._write(
            'DELETE', path, xa_user=xa_user, context=context)

        try:
            after = self.action_resources(request_number, action_id)
            verified = self._amount_in(after, resource_id, stage) is None
            detail = (f'{stage} line for resource {resource_id} '
                      f'{"gone" if verified else "still present"}')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='remove_resource', method='DELETE', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'resource_id': int(resource_id), 'stage': stage,
                   'context': context or XA_ADMIN_CONTEXT})

    def set_action_dates(self, request_id: int, action_id: int, begin: Any,
                         end: Any, *, request_number: str, xa_user: str,
                         context: Optional[str] = None) -> XrasWriteResult:
        """Create an allocation-date range. Returns the new ``allocationDateId``.

        Verified by re-reading and confirming a date with the requested
        begin/end exists on the action.
        """
        b, e = _date_str(begin), _date_str(end)
        params = {'beginDate': b, 'endDate': e}
        path = (f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
                f'/allocation_dates')
        before = self.action_dates(request_number, action_id)
        status, result, message, error = self._write(
            'POST', path, params=params, xa_user=xa_user, context=context)
        new_id = result.get('allocationDateId') if isinstance(result, dict) \
            else None

        try:
            after = self.action_dates(request_number, action_id)
            verified = any(_date_str(d.get('beginDate')) == b
                           and _date_str(d.get('endDate')) == e for d in after)
            detail = (f'dates {b}..{e} '
                      f'{"present" if verified else "not found"}')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='set_action_dates', method='POST', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'allocation_date_id': new_id, 'begin_date': b,
                   'end_date': e, 'context': context or XA_ADMIN_CONTEXT})

    def update_action_dates(self, request_id: int, action_id: int,
                            allocation_date_id: int, begin: Any, end: Any, *,
                            request_number: str, xa_user: str,
                            context: Optional[str] = None) -> XrasWriteResult:
        """Update one allocation-date range in place.

        Verified by re-reading the date with this id and confirming its
        begin/end match.
        """
        b, e = _date_str(begin), _date_str(end)
        params = {'beginDate': b, 'endDate': e}
        path = (f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
                f'/allocation_dates/{int(allocation_date_id)}')
        before = self.action_dates(request_number, action_id)
        status, _, message, error = self._write(
            'PUT', path, params=params, xa_user=xa_user, context=context)

        try:
            after = self.action_dates(request_number, action_id)
            match = next((d for d in after
                          if d.get('allocationDateId') == int(allocation_date_id)),
                         None)
            verified = (match is not None
                        and _date_str(match.get('beginDate')) == b
                        and _date_str(match.get('endDate')) == e)
            detail = (f'date {allocation_date_id} -> {b}..{e} '
                      f'{"confirmed" if verified else "not confirmed"}')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='update_action_dates', method='PUT', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'allocation_date_id': int(allocation_date_id),
                   'begin_date': b, 'end_date': e,
                   'context': context or XA_ADMIN_CONTEXT})

    def remove_action_dates(self, request_id: int, action_id: int,
                            allocation_date_id: int, *, request_number: str,
                            xa_user: str,
                            context: Optional[str] = None) -> XrasWriteResult:
        """Delete one allocation-date range.

        Verified by re-reading and confirming no date with this id remains.
        """
        path = (f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
                f'/allocation_dates/{int(allocation_date_id)}')
        before = self.action_dates(request_number, action_id)
        status, _, message, error = self._write(
            'DELETE', path, xa_user=xa_user, context=context)

        try:
            after = self.action_dates(request_number, action_id)
            verified = not any(
                d.get('allocationDateId') == int(allocation_date_id)
                for d in after)
            detail = (f'date {allocation_date_id} '
                      f'{"gone" if verified else "still present"}')
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='remove_action_dates', method='DELETE', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'allocation_date_id': int(allocation_date_id),
                   'context': context or XA_ADMIN_CONTEXT})

    # request attributes & action fields (the metadata editors)
    #
    # Both are `PUT` + query params + verify-by-reread, like everything above,
    # and both take **wire** field names (`shortTitle`, `userComments`) — the
    # caller maps snake_case->wire, so a field misspelling is a 400 the modal
    # renders, not a silent no-op. Only fields the reports feed echoes back are
    # editable here, because a field it does not return could not be verified.

    def _action(self, request_number: str,
                action_id: int) -> Optional[Dict[str, Any]]:
        """One action dict, via the reports family, or ``None``."""
        return next((a for a in self._actions(request_number)
                     if a.get('actionId') == action_id), None)

    @staticmethod
    def _fields_verified(source: Optional[Dict[str, Any]],
                         params: Dict[str, str]) -> bool:
        """Every sent field reads back equal (``None``/'' both mean empty).

        WARNING: Compared **whitespace-stripped**: XRAS normalizes leading/trailing
        whitespace on stored text (measured 2026-08-22 — a 1020-char abstract
        ending in a space read back at 1019). An exact match would then report
        an otherwise-successful write as *unverified*. A middle truncation still
        differs and is still caught.
        """
        if source is None:
            return False
        return all(str(source.get(k) or '').strip() == v.strip()
                   for k, v in params.items())

    def update_request_attributes(self, request_id: int, *,
                                  request_number: str, xa_user: str,
                                  context: Optional[str] = None,
                                  **fields: Any) -> XrasWriteResult:
        """Set request-level text attributes (``title``/``shortTitle``/``abstract``).

        ``fields`` are **wire-named**; an empty string clears one. Verified by
        re-reading the request and confirming each sent field matches.
        """
        params = {k: ('' if v is None else str(v)) for k, v in fields.items()}
        path = f'/v1/requests/{int(request_id)}/attributes'
        before_src = self._line(request_number, request_id)
        before = {k: before_src.get(k) for k in fields}
        status, _, message, error = self._write(
            'PUT', path, params=params, xa_user=xa_user, context=context)

        try:
            after_src = self._line(request_number, request_id)
            verified = self._fields_verified(after_src, params)
            after = {k: after_src.get(k) for k in fields}
            detail = 'attributes ' + ', '.join(
                f'{k}={after_src.get(k)!r}' for k in fields)
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='update_attributes', method='PUT', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'fields': list(fields),
                   'context': context or XA_ADMIN_CONTEXT})

    def update_action(self, request_id: int, action_id: int, *,
                      request_number: str, xa_user: str,
                      context: Optional[str] = None,
                      **fields: Any) -> XrasWriteResult:
        """Set action-level text fields (``userComments``).

        ``fields`` are **wire-named**; an empty string clears one. Verified by
        re-reading the action and confirming each sent field matches.
        """
        params = {k: ('' if v is None else str(v)) for k, v in fields.items()}
        path = f'/v1/requests/{int(request_id)}/actions/{int(action_id)}'
        before_src = self._action(request_number, action_id) or {}
        before = {k: before_src.get(k) for k in fields}
        status, _, message, error = self._write(
            'PUT', path, params=params, xa_user=xa_user, context=context)

        try:
            after_src = self._action(request_number, action_id)
            verified = self._fields_verified(after_src, params)
            after = ({k: after_src.get(k) for k in fields}
                     if after_src is not None else None)
            detail = 'action fields ' + ', '.join(
                f'{k}={(after_src or {}).get(k)!r}' for k in fields)
        except XrasSourceUnavailable as exc:
            after, verified, detail = None, None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='update_action', method='PUT', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=after, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': action_id,
                   'fields': list(fields), 'context': context or XA_ADMIN_CONTEXT})

    # destructive lifecycle (Tier A — ADMIN_XRAS only)
    #
    # WARNING: These are **irreversible in XRAS** and were **NOT live-probed** — a
    # delete cannot be tested without deleting something, and a renew/add-action
    # pollutes the request. They are shipped fail-visible: a single attempt, a
    # verifying read, and the same three-valued verdict as every verb above. If
    # our key does not authorize one, XRAS answers 401 and the modal renders it.
    # The route gates them on ADMIN_XRAS (effectively SYSTEM_ADMIN) and the write
    # lever, and confirms with hx-confirm.

    @staticmethod
    def _identity(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """A compact, low-PII snapshot of a request — what a delete destroys.

        Enough to know *what* was removed (number, id, status, title, action
        count) without dumping the roster's participant PII into the audit row.
        """
        if not isinstance(payload, dict):
            return None
        return {
            'requestNumber': payload.get('requestNumber'),
            'requestId': payload.get('requestId'),
            'requestStatus': payload.get('requestStatus'),
            'title': payload.get('title'),
            'action_count': len(payload.get('actions') or ()),
        }

    def delete_request(self, request_id: int, *, request_number: str,
                       xa_user: str,
                       context: Optional[str] = None) -> XrasWriteResult:
        """Delete a whole request. **Irreversible in XRAS.**

        Verified by: **this line** is absent from the reports family -- sibling
        lines (a New beside the deleted Renewal) still resolve and must not read
        as a failed delete. The pre-delete identity is captured for the audit
        row, because after this there is nothing left to read.
        """
        path = f'/v1/requests/{int(request_id)}'
        before = self._identity(self.reader.get_request_line(
            request_number, request_id=request_id))
        status, _, message, error = self._write(
            'DELETE', path, xa_user=xa_user, context=context)

        try:
            after = self.reader.get_request_line(request_number,
                                                 request_id=request_id)
            verified = after is None
            detail = ('line no longer resolves in the family' if verified
                      else 'line still resolves')
        except XrasSourceUnavailable as exc:
            verified, detail = None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='delete_request', method='DELETE', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before=before, after=None, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number,
                   'context': context or XA_ADMIN_CONTEXT})

    def renew_request(self, request_id: int, *, request_number: str,
                      xa_user: str,
                      context: Optional[str] = None) -> XrasWriteResult:
        """Spawn a renewal of a request.

        Verified by: the POST returns a **new** ``requestId`` (a renewal is a
        distinct request; the original stays). A 200 with no id read back is
        left ``unverified`` — an operator confirms in XRAS.
        """
        path = f'/v1/requests/{int(request_id)}/renew'
        status, result, message, error = self._write(
            'POST', path, xa_user=xa_user, context=context)
        new_id = result.get('requestId') if isinstance(result, dict) else None

        if error:
            verified, detail = None, f'renew errored: {error}'
        elif new_id is not None:
            verified = new_id != int(request_id)
            detail = f'renewal spawned as requestId {new_id}'
        else:
            verified, detail = None, 'no new requestId returned; confirm in XRAS'

        return XrasWriteResult(
            operation='renew_request', method='POST', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before={'requestId': int(request_id)},
            after={'renewalRequestId': new_id}, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'renewal_request_id': new_id,
                   'context': context or XA_ADMIN_CONTEXT})

    def add_action(self, request_id: int, action_type: str, *,
                   request_number: str, xa_user: str,
                   context: Optional[str] = None) -> XrasWriteResult:
        """Add a new action to a request.

        Verified by: a new action appears on the request (the returned
        ``actionId`` is present, or the action count grew).
        """
        path = f'/v1/requests/{int(request_id)}/actions'
        params = {'actionType': str(action_type)}
        before_count = len(self._actions(request_number))
        status, result, message, error = self._write(
            'POST', path, params=params, xa_user=xa_user, context=context)
        new_id = result.get('actionId') if isinstance(result, dict) else None

        try:
            after = self._actions(request_number)
            verified = ((new_id is not None
                         and any(a.get('actionId') == new_id for a in after))
                        or len(after) > before_count)
            detail = (f'action {new_id} added' if new_id is not None
                      else f'action count {before_count} -> {len(after)}')
        except XrasSourceUnavailable as exc:
            verified, detail = None, f'verify read failed: {exc}'

        return XrasWriteResult(
            operation='add_action', method='POST', path=path,
            xa_user=xa_user, http_status=status, message=message,
            before={'action_count': before_count},
            after={'action_id': new_id}, verified=verified,
            verify_detail=detail, write_error=error,
            extra={'request_number': request_number, 'action_id': new_id,
                   'action_type': action_type,
                   'context': context or XA_ADMIN_CONTEXT})
