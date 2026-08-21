"""Write client for the XRAS Allocations API — the deliberate sibling of ``client.py``.

Why a second class
------------------
:class:`~sam.integration.xras_api.client.XrasApiClient` is GET-only *by
construction*: its only transport primitive is ``_get`` and
``tests/unit/test_xras_api_client.py`` pins that no write verb exists on it.
That pin is worth keeping, so this is a **sibling, never a subclass and never a
relaxation** — the two classes share a config object and nothing else.

They also cannot be merged, because they live in different XRAS contexts. The
read client hardcodes ``XA-CONTEXT: report``; every write here needs
``submit``. The two are not interchangeable in either direction:

* the Reports family (``/v1/reports/*``) answers under ``report`` and **401s
  under ``submit``** — which is why verification reads that need a roster or an
  action state are delegated to a read client (:attr:`XrasAdminClient.reader`)
  rather than issued here;
* the write routes answer under ``submit``.

``GET /v1/people/<u>`` is the one route that answers under both (probe P0),
which is what lets merge verify itself on this client's own connection.

What this client may do, measured
---------------------------------
Every verb below was proven against production on 2026-08-21 and is recorded in
``docs/xras/outgoing/XRAS_WRITE_PROBES.md``. Three facts from that probe shape
this module and are not obvious from the published API docs:

1. **One authorization rule covers every request-scoped write**: ``XA-USER``
   must hold a role on *that* request, else 401. ``arcguest`` — the config
   default — is never sufficient. So request ops take an explicit *xa_user* and
   refuse to guess it, while person ops (merge) take none at all.
2. **``roleType`` is encoded differently by the two role families.** The route
   used here, ``/v1/requests/<rid>/roles/<roleType>/<username>``, takes the
   **string** (``User``) and 400s on the numeric id. :data:`ROLE_TYPES` carries
   all three representations so a caller cannot pick the wrong one silently.
3. **A 200 proves only that the call was allowed.** ``POST .../submit`` returns
   a ``null`` body where the docs promise the request object, and
   ``POST /v1/people`` returns 200 while ignoring the parameter it was given.
   So **every write here verifies by re-reading**, and the verdict travels in
   :class:`XrasWriteResult` rather than being collapsed into an exception.

Retry policy — the inverse of the read client
---------------------------------------------
Reads retry; **writes get exactly one attempt**. A retried merge could delete a
second person, and a retried submit could double-fire XRAS's review workflow.
When a write's outcome is ambiguous (a 5xx, or a socket that died mid-flight)
the answer is not another attempt — it is the verifying read, which runs
regardless and settles what actually happened. Only a definite refusal (4xx)
short-circuits it, because nothing happened to verify.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

import requests

from sam.integration.xras_api.base import (
    XrasSourceUnavailable,
    XrasWriteNotConfigured,
    XrasWriteRejected,
)
from sam.integration.xras_api.client import XrasApiClient, _unwrap
from sam.integration.xras_api.config import XrasApiConfig

logger = logging.getLogger(__name__)

#: Mirror of the read client's ``XA_CONTEXT``. Not a knob — see the module
#: docstring for why one class cannot serve both contexts.
XA_ADMIN_CONTEXT = 'submit'


@dataclass(frozen=True)
class RoleType:
    """One NCAR role type, in all three of the spellings the API uses.

    ``type_id`` is what the roster reports and the projcode-keyed route wants;
    ``name`` is what the route this client uses wants; ``display`` is XRAS's
    own operator vocabulary, which the UI should render so that SAM and the
    XRAS admin app read alike.
    """

    type_id: int
    name: str
    display: str


#: ``GET /v1/types/roles`` for the NCAR process, read live 2026-08-21. There is
#: no co-PI in this process. PRIVILEGE(#10): three spellings are carried only
#: because the two role families disagree on the encoding.
#: Hardcoded rather than fetched: it is three rows
#: that have not changed since the process opened, a wrong value here is a
#: 400 rather than a silent mis-write, and the alternative is a network call in
#: the path of rendering a form.
ROLE_TYPES: Tuple[RoleType, ...] = (
    RoleType(13, 'PI', 'Project Lead'),
    RoleType(14, 'Allocation Manager', 'Project Admin'),
    RoleType(19, 'User', 'User'),
)

#: The roleTypeId that owns a request. Withdraw, re-submit and role changes are
#: all authorized against a role-holder, and probe P2 showed the PI and the
#: Allocation Manager are **not** interchangeable — the same action validated
#: for the PI and failed for the Allocation Manager. Impersonate this one.
#: PRIVILEGE(#5): an ``admin``-context key might act as SAM itself and retire
#: the whole impersonation apparatus.
PI_ROLE_TYPE_ID = 13

_BY_ID = {r.type_id: r for r in ROLE_TYPES}
_BY_NAME = {r.name.casefold(): r for r in ROLE_TYPES}


def role_type(key: Any) -> RoleType:
    """Resolve a role type from an id, a wire name, or a :class:`RoleType`.

    Raises:
        ValueError: unknown role type. Deliberately loud — the alternative is
            posting an unrecognised value into a URL path.
    """
    if isinstance(key, RoleType):
        return key
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        found = _BY_ID.get(int(key))
    else:
        found = _BY_NAME.get(str(key).strip().casefold())
    if found is None:
        raise ValueError(f'unknown XRAS role type {key!r}; '
                         f'expected one of {[r.name for r in ROLE_TYPES]}')
    return found


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


class XrasAdminClient:
    """Single-attempt, self-verifying writes against the XRAS admin surface."""

    def __init__(self, config: Optional[XrasApiConfig] = None,
                 reader: Optional[XrasApiClient] = None) -> None:
        self.config = config or XrasApiConfig.from_environment()
        #: Report-context reads used for verification. A separate object
        #: because the Reports family 401s under ``submit`` — see the module
        #: docstring. PRIVILEGE(#2): a key that could read
        #: ``GET /v1/requests/<rid>`` would delete this whole delegate.
        self.reader = reader or XrasApiClient(self.config)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SAM/1.0 (+https://sam.hpc.ucar.edu)',
            'Accept': 'application/json',
            'XA-API-KEY': self.config.api_key,
            'XA-ALLOCATIONS-PROCESS': self.config.allocations_process,
            'XA-CONTEXT': XA_ADMIN_CONTEXT,
            'XA-USER': self.config.api_user,
        })

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

    # ── internals ───────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _headers(xa_user: Optional[str]) -> Optional[Dict[str, str]]:
        """Per-request impersonation.

        Passed to the individual call rather than mutated onto the session:
        one client instance serves several requests with different PIs, and a
        session-level ``XA-USER`` would leak whichever one was set last.
        """
        return {'XA-USER': xa_user} if xa_user else None

    def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
             xa_user: Optional[str] = None) -> Optional[Any]:
        """Submit-context GET. Retries 5xx like the read client; 404 → ``None``.

        Reads are idempotent, so this keeps the read client's retry policy —
        the single-attempt rule in this module applies to :meth:`_write`.
        """
        url = self._url(path)
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = self.session.request(
                    'GET', url, params=params, headers=self._headers(xa_user),
                    timeout=self.config.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.config.max_retries - 1:
                    break
                time.sleep(2 ** attempt)
                continue

            status = response.status_code
            if status == 404:
                return None
            if 400 <= status < 500:
                raise XrasWriteRejected(
                    f'{url} returned HTTP {status}: {response.text[:200]}',
                    status=status)
            if status >= 500:
                last_error = requests.HTTPError(f'HTTP {status}')
                if attempt == self.config.max_retries - 1:
                    break
                time.sleep(2 ** attempt)
                continue

            try:
                return _unwrap(response.json())
            except ValueError as exc:
                raise XrasSourceUnavailable(
                    f'{url} returned non-JSON body: {exc}') from exc

        raise XrasSourceUnavailable(
            f'{url} unreachable after {self.config.max_retries} attempts: '
            f'{last_error}')

    def _write(self, method: str, path: str, *,
               params: Optional[Mapping[str, Any]] = None,
               xa_user: Optional[str] = None
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
                method, url, params=params, headers=self._headers(xa_user),
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

    # ── verification helpers ────────────────────────────────────────────

    def _actions(self, request_number: str) -> List[Dict[str, Any]]:
        """Actions for a request, via the **reports** family (report context)."""
        payload = self.reader.get_request_by_number(request_number) or {}
        return [a for a in (payload.get('actions') or []) if isinstance(a, dict)]

    def action_status(self, request_number: str, action_id: int) -> Optional[str]:
        """The live ``actionStatus`` for one action, or ``None`` if not found."""
        for action in self._actions(request_number):
            if action.get('actionId') == action_id:
                return action.get('actionStatus')
        return None

    def roster(self, request_number: str) -> List[Dict[str, Any]]:
        """The request's roster, flattened to one row per *role*.

        PRIVILEGE(#4). ⚠️ The reports payload **nests**: each ``roles[]`` entry carries a
        ``person`` plus its own ``roles[]`` list of
        ``{roleId, role, roleTypeId, …}``. Reading ``roleType`` off the outer
        object returns ``None``, which is a trap worth flattening once here.
        """
        payload = self.reader.get_request_by_number(request_number) or {}
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

    def resolve_pi(self, request_number: str) -> Optional[str]:
        """The username of the request's PI — the default impersonation target."""
        for row in self.roster(request_number):
            if row.get('role_type_id') == PI_ROLE_TYPE_ID:
                return row.get('username')
        return None

    # ── people ──────────────────────────────────────────────────────────

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

    # ── actions ─────────────────────────────────────────────────────────

    def validate_action(self, request_id: int, action_id: int, *,
                        xa_user: str) -> Dict[str, Any]:
        """Preflight one action. ``{'validation': ..., 'errors': [...]}``.

        PRIVILEGE(#6). ⚠️ **The verdict is a function of *xa_user*, not only of
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

        ⚠️ The 200 from ``.../submit`` carries a ``null`` result where the API
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

    # ── roles ───────────────────────────────────────────────────────────

    def add_role(self, request_id: int, role, username: str, *,
                 request_number: str, xa_user: str) -> XrasWriteResult:
        """Put *username* on the request in *role*. Returns the new ``roleId``.

        ⚠️ **No person parameters are sent, ever.** This route accepts an
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
        before = self.roster(request_number)
        status, result, message, error = self._write('POST', path, xa_user=xa_user)
        role_id = result.get('roleId') if isinstance(result, dict) else None

        try:
            after = self.roster(request_number)
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
        before = self.roster(request_number)
        status, _, message, error = self._write('DELETE', path, xa_user=xa_user)

        try:
            after = self.roster(request_number)
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
