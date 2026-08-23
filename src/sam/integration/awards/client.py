"""Shared HTTP transport for award providers.

Modeled on ``collectors/lib/api_client.py``: one persistent
``requests.Session`` (connection reuse matters — a lookup can issue up to
three requests), an explicit timeout on every call, three attempts with
``2 ** attempt`` backoff, and **no retry on 4xx** — a 404 is an answer,
not a failure to answer.

Deliberately *not* the offline script's bare ``urllib.request.urlopen``
(``sql/queries/nsf_awards.py``): that one runs in a batch job where a
30-second stall is invisible, whereas this one runs inside a web request.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Optional

import requests

from sam.integration.awards.base import AwardSourceUnavailable

logger = logging.getLogger(__name__)

#: Seconds. Deliberately short — this runs inside an htmx round-trip, and
#: a slow agency API must degrade to "source unavailable" rather than hold
#: a worker.
DEFAULT_TIMEOUT = 10

DEFAULT_MAX_RETRIES = 3


class AwardHttpClient:
    """Retrying JSON client over a persistent session."""

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 user_agent: str = 'SAM/1.0 (+https://sam.hpc.ucar.edu)') -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent,
                                     'Accept': 'application/json'})

    # internals

    def _request(self, method: str, url: str, *,
                 json_body: Optional[Mapping[str, Any]] = None,
                 params: Optional[Mapping[str, Any]] = None) -> Optional[Any]:
        """Return the decoded JSON body, or ``None`` on a 404.

        Raises:
            AwardSourceUnavailable: every attempt failed, or the source
                answered 4xx (other than 404) / unparseable JSON.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method, url, json=json_body, params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** attempt)
                continue

            status = response.status_code
            if status == 404:
                return None
            if 400 <= status < 500:
                # A client error is deterministic — retrying cannot help.
                raise AwardSourceUnavailable(
                    f'{url} returned HTTP {status}: {response.text[:200]}')
            if status >= 500:
                last_error = requests.HTTPError(f'HTTP {status}')
                if attempt == self.max_retries - 1:
                    break
                wait = 2 ** attempt
                logger.warning('award source %s: HTTP %s, retry %d/%d in %ds',
                               url, status, attempt + 1, self.max_retries, wait)
                time.sleep(wait)
                continue

            try:
                return response.json()
            except ValueError as exc:
                raise AwardSourceUnavailable(
                    f'{url} returned non-JSON body: {exc}') from exc

        raise AwardSourceUnavailable(
            f'{url} unreachable after {self.max_retries} attempts: {last_error}')

    # public

    def get_json(self, url: str, *,
                 params: Optional[Mapping[str, Any]] = None) -> Optional[Any]:
        return self._request('GET', url, params=params)

    def post_json(self, url: str,
                  body: Mapping[str, Any]) -> Optional[Any]:
        return self._request('POST', url, json_body=body)
