"""Jira Service Management (Server/DC, REST v2 + ``servicedeskapi``) as a provider.

``JiraConfig`` reads three fail-closed levers' worth of keys; ``_JiraTransport``
is the HTTP half (reads retry 5xx, writes are ONE attempt because a retried
create is a duplicate ticket); ``JiraServiceDeskProvider`` composes a
transport, so tests inject a fake and a future Jira Software provider reuses
the transport without inheriting from this class. Cloud differs in auth
(``JIRA_AUTH=basic`` + ``JIRA_USER``) and the search path. Instance facts
(project RC, desk 3, request type 20): docs/plans/TICKET_PROVIDER.md § 2.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

import requests

from sam.integration._config import (config_bool, config_float, config_int,
                                     config_str)
from sam.integration.tickets.base import (KINDS, TicketDraft, TicketProvider,
                                          TicketRef,
                                          TicketRejected,
                                          TicketSourceUnavailable)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://ithelp.ucar.edu'
DEFAULT_PROJECT_KEY = 'RC'
DEFAULT_SERVICE_DESK_ID = '3'
DEFAULT_REQUEST_TYPE_ID = '20'
DEFAULT_LABELS = 'sam-account-request'
DEFAULT_TIMEOUT = 10.0
DEFAULT_CONNECT_TIMEOUT = 3.05
DEFAULT_MAX_RETRIES = 3
#: The webapp's budget: a filing runs inside a request, after its commit.
INTERACTIVE_TIMEOUT = 5.0
BEARER = 'bearer'
BASIC = 'basic'
AUTH_MODES = (BEARER, BASIC)


def _split_labels(raw: str) -> Tuple[str, ...]:
    return tuple(x for x in re.split(r'[\s,]+', raw) if x)


@dataclass(frozen=True)
class JiraKind:
    """Where one :data:`KINDS` name files: ``JIRA_<KIND>_{SERVICE_DESK_ID,
    REQUEST_TYPE_ID,LABELS}``, each falling back to the unsuffixed default."""

    service_desk_id: str
    request_type_id: str
    labels: Tuple[str, ...]

    @classmethod
    def from_environment(cls, kind: str, default: 'JiraKind') -> 'JiraKind':
        prefix = f'JIRA_{kind.upper()}_'
        labels = config_str(f'{prefix}LABELS', '')
        return cls(
            service_desk_id=config_str(f'{prefix}SERVICE_DESK_ID', '') or default.service_desk_id,
            request_type_id=config_str(f'{prefix}REQUEST_TYPE_ID', '') or default.request_type_id,
            labels=_split_labels(labels) if labels else default.labels)

    def summary(self) -> Dict[str, str]:
        return {'service_desk': self.service_desk_id, 'request_type': self.request_type_id,
                'labels': ', '.join(self.labels)}


@dataclass(frozen=True)
class JiraConfig:
    """A snapshot of ``JIRA_*`` config, resolved at construction. The unsuffixed
    desk / type / labels are the defaults every kind in ``kinds`` falls back to."""

    enabled: bool = False
    write_enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    token: str = ''
    auth: str = BEARER
    user: str = ''
    project_key: str = DEFAULT_PROJECT_KEY
    service_desk_id: str = DEFAULT_SERVICE_DESK_ID
    request_type_id: str = DEFAULT_REQUEST_TYPE_ID
    labels: Tuple[str, ...] = (DEFAULT_LABELS,)
    timeout: float = DEFAULT_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    kinds: Mapping[str, JiraKind] = field(default_factory=dict)

    @classmethod
    def from_environment(cls) -> 'JiraConfig':
        auth = config_str('JIRA_AUTH', BEARER).lower()
        config = cls(
            enabled=config_bool('JIRA_ENABLED', False),
            write_enabled=config_bool('JIRA_WRITE_ENABLED', False),
            base_url=(config_str('JIRA_BASE_URL', DEFAULT_BASE_URL)
                      or DEFAULT_BASE_URL).rstrip('/'),
            token=config_str('JIRA_TOKEN', ''),
            auth=auth if auth in AUTH_MODES else BEARER,
            user=config_str('JIRA_USER', ''),
            project_key=config_str('JIRA_PROJECT_KEY', DEFAULT_PROJECT_KEY) or DEFAULT_PROJECT_KEY,
            service_desk_id=(config_str('JIRA_SERVICE_DESK_ID', DEFAULT_SERVICE_DESK_ID)
                             or DEFAULT_SERVICE_DESK_ID),
            request_type_id=(config_str('JIRA_REQUEST_TYPE_ID', DEFAULT_REQUEST_TYPE_ID)
                             or DEFAULT_REQUEST_TYPE_ID),
            labels=_split_labels(config_str('JIRA_LABELS', DEFAULT_LABELS)),
            timeout=config_float('JIRA_TIMEOUT', DEFAULT_TIMEOUT),
            connect_timeout=config_float('JIRA_CONNECT_TIMEOUT', DEFAULT_CONNECT_TIMEOUT),
            max_retries=config_int('JIRA_MAX_RETRIES', DEFAULT_MAX_RETRIES),
        )
        default = JiraKind(config.service_desk_id, config.request_type_id, config.labels)
        return replace(config, kinds={k: JiraKind.from_environment(k, default) for k in KINDS})

    def for_kind(self, kind: str) -> JiraKind:
        """Raises ``ValueError`` for a name not in :data:`KINDS` (a caller bug)."""
        if kind not in KINDS:
            raise ValueError(f'unknown ticket kind {kind!r}; expected one of {", ".join(KINDS)}')
        return self.kinds.get(kind) or JiraKind(self.service_desk_id, self.request_type_id,
                                                self.labels)

    def interactive(self) -> 'JiraConfig':
        """The webapp budget: at most 5 s read, one attempt."""
        return replace(self, timeout=min(self.timeout, INTERACTIVE_TIMEOUT), max_retries=1)

    @property
    def configured(self) -> bool:
        """The read lever and a usable credential (Basic also needs a user)."""
        return bool(self.enabled and self.token
                    and (self.auth != BASIC or self.user))

    @property
    def write_configured(self) -> bool:
        """Writes need reads too: every create is preceded by a find."""
        return bool(self.configured and self.write_enabled)

    def summary(self) -> Dict[str, Any]:
        return {
            'enabled': self.enabled,
            'write_enabled': self.write_enabled,
            'token_set': bool(self.token),
            'base_url': self.base_url,
            'auth': self.auth,
            'user': self.user,
            'project': self.project_key,
            'service_desk': self.service_desk_id,
            'request_type': self.request_type_id,
            'labels': ', '.join(self.labels),
            'timeout': self.timeout,
            'connect_timeout': self.connect_timeout,
            'max_retries': self.max_retries,
            'configured': self.configured,
            'write_configured': self.write_configured,
            'kinds': {k: self.for_kind(k).summary() for k in KINDS},
        }


def _error_lines(response: 'requests.Response') -> List[str]:
    """Jira's ``errorMessages`` + ``errors``, or JSM's ``errorMessage``."""
    try:
        body = response.json()
    except ValueError:
        return [response.text[:200]] if response.text else []
    if not isinstance(body, Mapping):
        return []
    lines = [str(m) for m in body.get('errorMessages') or ()]
    lines += [f'{k}: {v}' for k, v in (body.get('errors') or {}).items()]
    if body.get('errorMessage'):
        lines.append(str(body['errorMessage']))
    return lines


class _JiraTransport:
    """Persistent session, (connect, read) timeouts, the retry policy."""

    def __init__(self, config: JiraConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SAM/1.0 (+https://sam.hpc.ucar.edu)',
            'Accept': 'application/json',
        })
        if config.auth == BASIC:
            self.session.auth = (config.user, config.token)
        else:
            self.session.headers['Authorization'] = f'Bearer {config.token}'

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{path.lstrip('/')}"

    def _timeout(self) -> Tuple[float, float]:
        return self.config.connect_timeout, self.config.timeout

    def _reject(self, method: str, url: str, response) -> TicketRejected:
        status = response.status_code
        errors = _error_lines(response)
        reason = ('token rejected' if status in (401, 403)
                  else '; '.join(errors) or response.reason or 'refused')
        return TicketRejected(f'{method} {url} -> HTTP {status}: {reason}',
                              status=status, errors=errors)

    def get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
            missing_ok: bool = False) -> Optional[Any]:
        """Parsed JSON. A 404 is ``None`` only with ``missing_ok`` (an issue that
        is gone); on a search or probe path it is a misconfiguration and raises.
        Retries socket errors and 5xx."""
        url = self._url(path)
        last: Optional[Exception] = None
        attempts = max(1, self.config.max_retries)
        for attempt in range(attempts):
            try:
                response = self.session.request('GET', url, params=params,
                                                timeout=self._timeout())
            except requests.RequestException as exc:
                last = exc
            else:
                status = response.status_code
                if status == 404 and missing_ok:
                    return None
                if 400 <= status < 500:
                    raise self._reject('GET', url, response)
                if status < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise TicketSourceUnavailable(
                            f'GET {url} returned a non-JSON body: {exc}') from exc
                last = requests.HTTPError(f'HTTP {status}')
            if attempt < attempts - 1:
                logger.warning('jira GET %s: %s, retry %d/%d', url, last,
                               attempt + 1, attempts)
                time.sleep(2 ** attempt)
        raise TicketSourceUnavailable(
            f'GET {url} failed after {attempts} attempt(s): {last}')

    def send(self, method: str, path: str, body: Mapping[str, Any], *,
             headers: Optional[Mapping[str, str]] = None) -> Optional[Any]:
        """One attempt, never retried. Parsed JSON, or ``None`` for an empty 2xx."""
        url = self._url(path)
        try:
            response = self.session.request(method, url, json=body, headers=headers,
                                            timeout=self._timeout())
        except requests.RequestException as exc:
            raise TicketSourceUnavailable(f'{method} {url}: {exc}') from exc
        status = response.status_code
        if 400 <= status < 500:
            raise self._reject(method, url, response)
        if status >= 400:
            raise TicketSourceUnavailable(f'{method} {url} -> HTTP {status}')
        if status == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


#: JSM experimental-API opt-in; harmless where the endpoint is stable.
_SD_HEADERS = {'X-ExperimentalApi': 'opt-in'}


def _jql_string(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


class JiraServiceDeskProvider(TicketProvider):
    """Files a JSM request of one configured type; reads through REST v2."""

    name = 'jira-servicedesk'

    def __init__(self, config: Optional[JiraConfig] = None,
                 transport: Optional[Any] = None) -> None:
        self.config = config or JiraConfig.from_environment()
        self._transport = transport

    @classmethod
    def from_environment(cls, *, interactive: bool = False) -> 'JiraServiceDeskProvider':
        config = JiraConfig.from_environment()
        return cls(config.interactive() if interactive else config)

    @property
    def transport(self) -> Any:
        """Built on first use, so a provider that never calls out opens no session."""
        if self._transport is None:
            self._transport = _JiraTransport(self.config)
        return self._transport

    @property
    def configured(self) -> bool:
        return self.config.configured

    @property
    def write_configured(self) -> bool:
        return self.config.write_configured

    @property
    def destination(self) -> str:
        return self.config.project_key

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, **self.config.summary()}

    def browse_url(self, key: str) -> str:
        return f'{self.config.base_url}/browse/{quote(key)}'

    @staticmethod
    def description(draft: TicketDraft) -> str:
        """``{noformat}`` keeps aligned columns; the link stays outside so it is clickable."""
        body = draft.body.replace('{noformat}', '{ noformat}').rstrip()
        text = f'{{noformat}}\n{body}\n{{noformat}}'
        return f'{text}\n\nSAM request: {draft.link_url}' if draft.link_url else text

    def create(self, draft: TicketDraft) -> TicketRef:
        self.guard_write()
        spec = self.config.for_kind(draft.kind)
        payload: Dict[str, Any] = {
            'serviceDeskId': spec.service_desk_id,
            'requestTypeId': spec.request_type_id,
            'requestFieldValues': {'summary': draft.summary,
                                   'description': self.description(draft)},
        }
        if draft.on_behalf_of:
            payload['raiseOnBehalfOf'] = draft.on_behalf_of
        data = self.transport.send('POST', '/rest/servicedeskapi/request', payload,
                                   headers=_SD_HEADERS) or {}
        key = data.get('issueKey') if isinstance(data, Mapping) else None
        if not key:
            raise TicketSourceUnavailable('JSM create answered without an issueKey')
        status = ((data.get('currentStatus') or {}).get('status') or '')
        logger.info('jira: filed %s (%s)', key, draft.handle)
        self._add_labels(key, tuple(dict.fromkeys(spec.labels + draft.labels)))
        self.post_automation_note(key, draft)
        return TicketRef(key=key, url=self.browse_url(key), status=status, closed=False)

    def _add_labels(self, key: str, labels: Tuple[str, ...]) -> None:
        """Best-effort: JSM rejects unknown request fields, so labels go on after."""
        if not labels:
            return
        try:
            self.transport.send('PUT', f'/rest/api/2/issue/{quote(key)}',
                                {'update': {'labels': [{'add': x} for x in labels]}})
        except TicketSourceUnavailable as exc:
            logger.warning('jira: labels on %s failed: %s', key, exc)

    def comment(self, key: str, text: str, *, internal: bool = True) -> None:
        self.guard_write()
        self.transport.send('POST', f'/rest/servicedeskapi/request/{quote(key)}/comment',
                            {'body': text, 'public': not internal}, headers=_SD_HEADERS)

    def get(self, key: str) -> Optional[TicketRef]:
        self.guard_read()
        data = self.transport.get(f'/rest/api/2/issue/{quote(key)}',
                                  params={'fields': 'status'}, missing_ok=True)
        if not data:
            return None
        return self._ref(data)

    def find(self, handle: str) -> Optional[TicketRef]:
        """Oldest exact-handle hit; a second hit is the cutover-duplicate signal."""
        self.guard_read()
        data = self.transport.get('/rest/api/2/search', params={
            'jql': self.find_jql(handle),
            'fields': 'status,summary,created',
            'maxResults': 10,
        }) or {}
        exact = re.compile(re.escape(handle) + r'(?!\d)')
        hits = [i for i in data.get('issues') or ()
                if exact.search((i.get('fields') or {}).get('summary') or '')]
        if len(hits) > 1:
            logger.warning('jira: %d tickets carry %s: %s', len(hits), handle,
                           ', '.join(i.get('key', '?') for i in hits))
        return self._ref(hits[0]) if hits else None

    def find_jql(self, handle: str) -> str:
        return (f'project = {self.config.project_key} AND summary ~ '
                f'"\\"{_jql_string(handle)}\\"" ORDER BY created ASC')

    def check(self) -> Tuple[bool, str]:
        try:
            self.guard_read()
            me = self.transport.get('/rest/api/2/myself') or {}
        except TicketSourceUnavailable as exc:
            return False, str(exc)
        return True, str(me.get('displayName') or me.get('name') or '')

    def _ref(self, issue: Mapping[str, Any]) -> TicketRef:
        key = issue.get('key', '')
        status = (issue.get('fields') or {}).get('status') or {}
        category = (status.get('statusCategory') or {}).get('key')
        return TicketRef(key=key, url=self.browse_url(key),
                         status=status.get('name') or '',
                         closed=(category == 'done') if category else None)


__all__ = ['JiraConfig', 'JiraKind', 'JiraServiceDeskProvider']
