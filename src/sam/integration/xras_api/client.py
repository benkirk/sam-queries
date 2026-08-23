"""GET-only HTTP client for the XRAS Allocations API (``https://api.xras.org/v1/...``).

The opposite direction from everything under ``src/sam/xras/`` and
``src/webapp/api/xras/``: this is SAM calling OUT. See ``docs/xras/outgoing/``.

Transport semantics copy ``sam.integration.awards.client``: one persistent
``requests.Session``, explicit timeout, three attempts with ``2 ** attempt``
backoff, and no retry on 4xx -- a 404 is an answer, not a failure to answer.

WARNING: GET-only is STRUCTURAL, not conventional. The documented API is far
more write-capable than this client, and our key holds at least some of it:
creating and deleting requests, submitting and withdrawing actions, adding and
removing roles, **merging one person into another**. The sole transport
primitive is :meth:`_get`, there is no generic verb method, and
``tests/unit/test_xras_api_client.py`` pins that no post/put/patch/delete
callable exists on the class.

``XA-CONTEXT`` is hardcoded to ``report`` and is not a knob: the Reports family,
the entire reason this client is useful, answers only under ``report`` and 401s
under ``submit``. ``XA-USER`` is required on every call but scopes nothing
outside ``/v1/requests``; reports return process-wide data whatever it says.

Every JSON response wraps its payload in ``{"message":..., "result":...}``,
unwrapped centrally in :meth:`_get`.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Mapping, Optional
from urllib.parse import quote

import requests

from sam.integration.xras_api.base import XrasApiNotConfigured, XrasSourceUnavailable
from sam.integration.xras_api.config import XrasApiConfig

logger = logging.getLogger(__name__)

#: See the module docstring. Not a parameter, by design.
XA_CONTEXT = 'report'

#: ``GET /v1/reports/requests`` filter vocabulary, verified against the live
#: NCAR process. ``status`` and ``active`` are mutually exclusive there.
REQUEST_STATUSES = ('Submitted', 'Approved', 'Rejected', 'Incomplete',
                    'Under Review')

DEFAULT_PAGE_SIZE = 50

#: Opportunity ids per ``/v1/opportunities/list/:ids`` call. They travel in
#: the path, so this bounds URL length rather than response size.
_OPPORTUNITY_CHUNK = 50


class _XrasTransport:
    """Shared transport for the two XRAS API clients — everything that does
    not depend on the read-vs-write context.

    The read client (:class:`XrasApiClient`) and the write client
    (:class:`~sam.integration.xras_api.admin_client.XrasAdminClient`) share a
    config object, a persistent session, the URL builder, and one retrying
    GET primitive. They differ in exactly three places, each a hook here: the
    session's ``XA-CONTEXT`` (:attr:`_CONTEXT`), the exception a 4xx raises
    (:meth:`_client_error`), and — writes only — per-call header overrides
    (:meth:`_headers`).

    This base carries **no write verb**: its sole transport primitive is
    :meth:`_get`. The write half lives on the admin subclass as ``_write``,
    which is exactly why ``XrasApiClient`` stays GET-only by construction while
    reusing this scaffolding. ``tests/unit/test_xras_api_client.py`` pins that
    invariant against this class.
    """

    #: The session-level ``XA-CONTEXT``. ``report`` reads, ``submit`` writes —
    #: see each subclass. Not a per-call knob; per-call overrides travel
    #: through :meth:`_headers`.
    _CONTEXT: str = 'report'

    def __init__(self, config: Optional[XrasApiConfig] = None) -> None:
        self.config = config or XrasApiConfig.from_environment()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SAM/1.0 (+https://sam.hpc.ucar.edu)',
            'Accept': 'application/json',
            'XA-API-KEY': self.config.api_key,
            'XA-ALLOCATIONS-PROCESS': self.config.allocations_process,
            'XA-CONTEXT': self._CONTEXT,
            'XA-USER': self.config.api_user,
        })

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _headers(xa_user: Optional[str] = None,
                 context: Optional[str] = None) -> Optional[Dict[str, str]]:
        """Per-request impersonation and (optionally) context override.

        Returns ``None`` when neither is set — the read client passes neither,
        so its calls carry no per-request headers exactly as before. Passed to
        the individual call rather than mutated onto the session: one client
        instance serves several requests with different PIs, and a
        session-level ``XA-USER`` would leak whichever one was set last. The
        same is true of ``XA-CONTEXT`` — the write client's session default is
        ``submit`` and the Approved-stage editors override it per call with
        ``admin`` rather than mutating the shared session.
        """
        headers: Dict[str, str] = {}
        if xa_user:
            headers['XA-USER'] = xa_user
        if context:
            headers['XA-CONTEXT'] = context
        return headers or None

    def _client_error(self, url: str, status: int,
                      response: 'requests.Response') -> None:
        """Raise the subclass's 4xx exception.

        A 4xx is deterministic — retrying cannot help, and for a write nothing
        happened to verify. The read client raises :class:`XrasSourceUnavailable`
        (this default); the write client overrides to raise
        :class:`~sam.integration.xras_api.base.XrasWriteRejected` carrying the
        status.
        """
        raise XrasSourceUnavailable(
            f'{url} returned HTTP {status}: {response.text[:200]}')

    def _get(self, path: str, *,
             params: Optional[Mapping[str, Any]] = None,
             xa_user: Optional[str] = None,
             context: Optional[str] = None) -> Optional[Any]:
        """Return the unwrapped ``result``, or ``None`` when XRAS has no such thing.

        The only transport primitive shared by both clients. Reads are
        idempotent, so this retries 5xx with ``2 ** attempt`` backoff; a 404 is
        an answer (``None``), and a 4xx is a deterministic refusal routed
        through :meth:`_client_error`.

        Raises:
            XrasSourceUnavailable: every attempt failed, or an unparseable
                body. A 4xx raises whatever :meth:`_client_error` raises.
        """
        url = self._url(path)
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = self.session.request(
                    'GET', url, params=params,
                    headers=self._headers(xa_user, context),
                    timeout=self.config.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.config.max_retries - 1:
                    break
                time.sleep(2 ** attempt)
                continue

            status = response.status_code
            if status == 404:
                # A Rails HTML 404 means "no such route"; a JSON body means
                # the route exists and found nothing. Both are "no such
                # thing" to a caller, and neither is worth a retry.
                logger.info('xras api GET %s -> 404', url)
                return None
            if 400 <= status < 500:
                # A client error is deterministic — retrying cannot help.
                # 401 here means the key is not provisioned for what was
                # asked, which is a configuration fact, not an outage.
                self._client_error(url, status, response)
            if status >= 500:
                last_error = requests.HTTPError(f'HTTP {status}')
                if attempt == self.config.max_retries - 1:
                    break
                wait = 2 ** attempt
                logger.warning('xras api %s: HTTP %s, retry %d/%d in %ds',
                               url, status, attempt + 1,
                               self.config.max_retries, wait)
                time.sleep(wait)
                continue

            try:
                body = response.json()
            except ValueError as exc:
                raise XrasSourceUnavailable(
                    f'{url} returned non-JSON body: {exc}') from exc

            logger.info('xras api GET %s -> %s', url, status)
            return _unwrap(body)

        raise XrasSourceUnavailable(
            f'{url} unreachable after {self.config.max_retries} attempts: '
            f'{last_error}')


class XrasApiClient(_XrasTransport):
    """Retrying JSON client over a persistent session. Reads only.

    Inherits the shared transport (:class:`_XrasTransport`) unchanged and adds
    only read verbs — there is deliberately no write primitive here (see the
    module docstring and ``tests/unit/test_xras_api_client.py``). The base's
    default :meth:`_client_error` — raising :class:`XrasSourceUnavailable` — is
    exactly the read behavior, so this class does not override it.
    """

    #: See the module docstring: the Reports family answers only under
    #: ``report`` and 401s under ``submit``. Not a knob.
    _CONTEXT = XA_CONTEXT

    @classmethod
    def from_environment(cls, config: Optional[XrasApiConfig] = None
                         ) -> 'XrasApiClient':
        """Build a configured client, or refuse.

        Raises:
            XrasApiNotConfigured: the lever is off or no key is set. It is a
                subclass of :class:`XrasSourceUnavailable`, so a caller that
                only handles "could not ask" is already correct.
        """
        resolved = config or XrasApiConfig.from_environment()
        if not resolved.configured:
            raise XrasApiNotConfigured(
                'XRAS outgoing API is not configured '
                '(needs XRAS_OUTGOING_ENABLED=1 and XRAS_API_KEY)')
        return cls(resolved)

    # people

    def get_person(self, username: str) -> Optional[Dict[str, Any]]:
        """One person from the global XRAS directory, or ``None`` if unknown.

        Carries what account creation needs and the inbound payload does not:
        ``residenceCountry``, ``academicStatus``, ``organization``, and
        ``isReconciled``. Researchers resolve here under their ARC placeholder
        username (``<name>-user-<token>``) whether or not they are reconciled.

        WARNING: ``isReconciled`` says XRAS has linked this username to a real
        identity — **not** that SAM has an account. Measured 9 of 9 on the
        local smoke: every worklist row was reconciled and every one still
        needed an account created or reactivated. See
        :func:`sam.queries.xras_accounts.enrich_worklist`.
        """
        return _as_dict(self._get(f'/v1/people/{quote(str(username), safe="")}'))

    def search_people(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """Directory search on name/username fragments. Same person shape."""
        return _as_list(self._get('/v1/search/people', params={'q': query}))

    def get_person_roles(self, username: str) -> Optional[Dict[str, Any]]:
        """Every request this person holds a role on, or ``None`` if unknown.

        ``GET /v1/reports/username/<username>`` — a report-context route, 200 on
        our key. Returns ``{panels, requestRoles}`` where ``requestRoles`` is a
        list of ``{roleName, requests[]}`` groups; each request carries
        ``requestNumber`` (the projcode), ``requestId``, ``requestTitle``,
        ``actionType``, ``allocationType``, ``opportunity`` and its dates — but
        **no** ``requestStatus`` (probed 2026-08-22, see
        ``docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md`` § 3.2a). A merged-away
        placeholder ``404``s here exactly as :meth:`get_person` does.
        """
        return _as_dict(
            self._get(f'/v1/reports/username/{quote(str(username), safe="")}'))

    # resources

    def get_resources(self) -> Optional[List[Dict[str, Any]]]:
        """The process's resource catalog, including ``resourceRepositoryKey``.

        That key is the join to ``xras_resource_repository_key_resource`` and
        is what makes ``sam-admin xras --validate-mapping`` two-sided: without
        it the audit can only see the keys SAM knows, never the ones XRAS will
        actually send.
        """
        return _as_list(self._get('/v1/resources'))

    # opportunities

    def get_open_opportunities(self) -> List[Dict[str, Any]]:
        """Every **currently open** opportunity, in full.

        WARNING: **This is the only way to see an opportunity nobody has submitted
        against yet**, and that is the whole reason it exists. The sweep's other
        source of opportunity ids is ``reports/requests``, which by construction
        cannot mention an opportunity with no requests — so a brand-new one is
        invisible there until its first request is *approved*, which may be
        weeks later.

        Measured: `Large Allocation (University) - Fall 2026` (535388) was
        posted and returned here immediately, while the Approved enumeration
        knew nothing of it.

        Complements :meth:`get_opportunities`, which resolves ids that are
        already known but may be closed. Neither subsumes the other: this one
        sees the future, that one sees the past.
        """
        return _as_list(self._get('/v1/opportunities')) or []

    def get_opportunities(self, opportunity_ids) -> List[Dict[str, Any]]:
        """Resolve opportunities by id, **including closed and Terminating ones**.

        ``GET /v1/opportunities/list/:ids``. The plain ``/v1/opportunities``
        route lists only what is *open* — five opportunities today — which is
        useless for this job: of the 27 the NCAR process has ever run, 22 are
        closed, and every one of them can still arrive on an inbound action.

        This is the only reason SAM calls out for opportunity data at all. A
        ``reports/requests`` row carries ``opportunityId`` and
        ``opportunity_name`` and nothing else — no ``allocationTypeId``, no
        panels — so the sweep cannot derive a mapping from the enumeration it
        already has.

        Chunked because the ids go in the **path**, not a query string, and a
        long path is the one thing a proxy in front of the API is most likely
        to truncate. 50 ids is roughly 350 characters.

        Returns the flattened list; ids XRAS does not know are simply absent,
        which is the same shape as asking about none of them.
        """
        wanted = [int(i) for i in opportunity_ids]
        if not wanted:
            # No request at all rather than `/list/` with an empty path segment,
            # which is a different route and answers 404.
            return []

        found: List[Dict[str, Any]] = []
        for start in range(0, len(wanted), _OPPORTUNITY_CHUNK):
            chunk = wanted[start:start + _OPPORTUNITY_CHUNK]
            path = '/v1/opportunities/list/' + ','.join(str(i) for i in chunk)
            found.extend(_as_list(self._get(path)) or [])
        return found

    def get_opportunity(self, opportunity_id) -> Optional[Dict[str, Any]]:
        """One opportunity in full, or ``None`` — ``GET /v1/opportunities/:id``.

        The single-id form, distinct from :meth:`get_opportunities` (the
        ``/list/:ids`` batch the sweep uses to resolve allocation types). This
        one backs the detail modal: it carries ``allocationTypeInfo`` (with the
        human ``description``), ``opportunityType``, ``defaultAllocationAwardPeriod``,
        ``announcementDate``, ``opportunityStates``, ``panels`` and
        ``attributeSets`` — everything the modal shows and the ``reports``
        enumeration's bare ``opportunityId``/``opportunity_name`` does not.
        """
        return _as_dict(self._get(f'/v1/opportunities/{int(opportunity_id)}'))

    # vocabularies

    def get_fos_types(self) -> Optional[List[Dict[str, Any]]]:
        """The field-of-science catalog — ``GET /v1/types/fos``.

        ~39 rows of ``{fosTypeId, fosName, fosAbbr, fosNum, fosTypeParentId,
        isSelectable}``. The join the request modal needs: a
        ``reports/request_numbers`` payload spells its ``fos[]`` as
        **ids only** (``fosTypeId``/``fosNum``, no name), so without this the
        modal can only render "FoS 30". Small and near-static — cached a day.
        """
        return _as_list(self._get('/v1/types/fos'))

    # requests (the Reports family)

    def get_request_by_number(self, request_number: str
                              ) -> Optional[Dict[str, Any]]:
        """Look up one request **by request number — i.e. by projcode**.

        ``GET /v1/requests/<requestNumber>`` is *not* this: that route is keyed
        on ``requestId`` and 401s for a number. This is the reports path.
        """
        result = self._get(
            f'/v1/reports/request_numbers/{quote(str(request_number), safe="")}')
        if isinstance(result, list):
            return result[0] if result else None
        return _as_dict(result)

    def get_requests_page(self, *, status: Optional[str] = 'Approved',
                          limit: int = DEFAULT_PAGE_SIZE,
                          prev_min_request_id: Optional[int] = None
                          ) -> List[Dict[str, Any]]:
        """One page of ``GET /v1/reports/requests``, newest ``requestId`` first.

        Unscoped: every request in the NCAR process, not just those the
        ``XA-USER`` holds a role on. Each row carries its full ``roles[]`` with
        the **person object inline** — which is why the enumeration feed never
        needs a separate ``/v1/people`` call.
        """
        params: Dict[str, Any] = {'limit': limit}
        if status:
            params['status'] = status
        if prev_min_request_id is not None:
            # Strictly-less-than, so the smallest id on the page is what asks
            # for the next one.
            params['prevMinRequestId'] = prev_min_request_id
        return _as_list(self._get('/v1/reports/requests', params=params)) or []

    def iter_request_pages(self, *, status: Optional[str] = 'Approved',
                           page_size: int = DEFAULT_PAGE_SIZE,
                           max_pages: Optional[int] = None
                           ) -> Iterator[List[Dict[str, Any]]]:
        """Paginate ``reports/requests``, yielding whole pages.

        The page-level primitive exists so a caller can *count* pages and know
        whether it stopped because the data ran out or because it hit
        *max_pages* — a silent cap reads as "covered everything" when it did
        not. :meth:`iter_requests` is the flattened convenience over this.

        Stops on an empty page, on *max_pages*, or if the cursor fails to
        advance (a defensive guard: a server that repeated a page would
        otherwise loop forever).
        """
        cursor: Optional[int] = None
        pages = 0
        while max_pages is None or pages < max_pages:
            rows = self.get_requests_page(status=status, limit=page_size,
                                          prev_min_request_id=cursor)
            if not rows:
                return
            pages += 1
            yield rows

            ids = [r.get('requestId') for r in rows
                   if isinstance(r, dict) and isinstance(r.get('requestId'), int)]
            if not ids:
                return
            lowest = min(ids)
            if cursor is not None and lowest >= cursor:
                logger.warning('xras api reports/requests cursor did not '
                               'advance (%s -> %s); stopping', cursor, lowest)
                return
            cursor = lowest
            if len(rows) < page_size:
                return

    def iter_requests(self, *, status: Optional[str] = 'Approved',
                      page_size: int = DEFAULT_PAGE_SIZE,
                      max_pages: Optional[int] = None
                      ) -> Iterator[Dict[str, Any]]:
        """Every request in the process, flattened across pages."""
        for page in self.iter_request_pages(status=status, page_size=page_size,
                                            max_pages=max_pages):
            for row in page:
                if isinstance(row, dict):
                    yield row


# envelope helpers

def _unwrap(body: Any) -> Any:
    """Strip the ``{"message": ..., "result": ...}`` envelope.

    A body that is not an envelope is returned untouched — the envelope is a
    consistent XRAS convention, not something worth failing over.
    """
    if isinstance(body, dict) and 'result' in body:
        return body['result']
    return body


def _as_list(value: Any) -> Optional[List[Any]]:
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None
