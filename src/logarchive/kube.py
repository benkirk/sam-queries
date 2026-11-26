"""The two Kubernetes API calls the archiver makes, over `requests`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

SA_DIR = Path('/var/run/secrets/kubernetes.io/serviceaccount')
CONNECT_TIMEOUT = 10
LIST_TIMEOUT = 30
#: A quiet pod sends nothing; the follower reconnects when this expires.
STREAM_READ_TIMEOUT = 300


class KubeError(Exception):
    """The API refused or could not be reached; the caller backs off and retries."""


class PodGone(KubeError):
    """The pod no longer exists (404): nothing more can be fetched from it."""


class StreamBroken(KubeError):
    """A log stream timed out or dropped mid-read. Routine: reconnect and resume."""


@dataclass(frozen=True)
class ContainerRef:
    name: str
    started: bool     # has produced a log the API will serve
    finished: bool    # terminated and never restarted: no further line can arrive


@dataclass(frozen=True)
class PodRef:
    name: str
    uid: str
    phase: str
    containers: tuple[ContainerRef, ...]

    def container(self, name: str) -> ContainerRef | None:
        return next((c for c in self.containers if c.name == name), None)


def _container_ref(spec: dict, status: dict | None, *, init: bool,
                   restart_policy: str, phase: str) -> ContainerRef:
    state = (status or {}).get('state') or {}
    terminated = state.get('terminated')
    started = bool(status) and ('waiting' not in state or status.get('restartCount', 0) > 0)
    if init:
        # A sidecar (an init container with its own restartPolicy: Always) restarts.
        restarts = spec.get('restartPolicy') == 'Always' and phase not in ('Succeeded', 'Failed')
        finished = bool(terminated) and not restarts and (
            terminated.get('exitCode') == 0 or restart_policy == 'Never'
            or phase in ('Succeeded', 'Failed'))
    else:
        finished = bool(terminated) and (
            phase in ('Succeeded', 'Failed') or restart_policy == 'Never'
            or (restart_policy == 'OnFailure' and terminated.get('exitCode') == 0))
    return ContainerRef(spec['name'], started, finished)


def pod_ref(item: dict) -> PodRef:
    """One item of a pod list, reduced to what the follower needs."""
    spec, status = item.get('spec') or {}, item.get('status') or {}
    phase = status.get('phase', 'Unknown')
    policy = spec.get('restartPolicy', 'Always')
    containers = []
    for key, status_key, init in (('initContainers', 'initContainerStatuses', True),
                                  ('containers', 'containerStatuses', False)):
        by_name = {s['name']: s for s in status.get(status_key) or []}
        for c in spec.get(key) or []:
            containers.append(_container_ref(c, by_name.get(c['name']), init=init,
                                             restart_policy=policy, phase=phase))
    meta = item['metadata']
    return PodRef(meta['name'], meta.get('uid', ''), phase, tuple(containers))


class KubeClient:
    def __init__(self, base_url: str, namespace: str, *, token_path: Path,
                 verify: str | bool = True, session: requests.Session | None = None):
        self.base_url = base_url.rstrip('/')
        self.namespace = namespace
        self.token_path = Path(token_path)
        self.verify = verify
        self.session = session or requests.Session()
        self._token = self._read_token()

    @classmethod
    def from_cluster(cls, sa_dir: Path = SA_DIR) -> 'KubeClient':
        host = os.environ['KUBERNETES_SERVICE_HOST']
        port = os.environ.get('KUBERNETES_SERVICE_PORT', '443')
        if ':' in host:   # a bare IPv6 address needs brackets in a URL
            host = f'[{host}]'
        return cls(f'https://{host}:{port}', (sa_dir / 'namespace').read_text().strip(),
                   token_path=sa_dir / 'token', verify=str(sa_dir / 'ca.crt'))

    def _read_token(self) -> str:
        return self.token_path.read_text().strip()

    def _get(self, path: str, params: dict, *, stream: bool = False, read_timeout: int = LIST_TIMEOUT):
        url = f'{self.base_url}/api/v1/namespaces/{self.namespace}/{path}'
        for attempt in (1, 2):
            try:
                resp = self.session.get(
                    url, params=params, stream=stream, verify=self.verify,
                    headers={'Authorization': f'Bearer {self._token}'},
                    timeout=(CONNECT_TIMEOUT, read_timeout))
            except requests.RequestException as exc:
                raise KubeError(f'GET {path}: {exc}') from exc
            if resp.status_code == 401 and attempt == 1:
                # Projected service-account tokens rotate; the file is always current.
                resp.close()
                self._token = self._read_token()
                continue
            break
        if resp.status_code == 404:
            resp.close()
            raise PodGone(f'GET {path}: 404')
        if resp.status_code != 200:
            body = resp.text[:200]
            resp.close()
            raise KubeError(f'GET {path}: {resp.status_code} {body}')
        return resp

    def list_pods(self, label_selector: str) -> list[PodRef]:
        resp = self._get('pods', {'labelSelector': label_selector})
        try:
            return [pod_ref(item) for item in resp.json().get('items') or []]
        except (ValueError, KeyError) as exc:
            raise KubeError(f'GET pods: unreadable list ({exc})') from exc

    def stream_log(self, pod: str, container: str, since_time: str | None = None
                   ) -> Iterator[tuple[str, str]]:
        """Yield (timestamp, line) to a clean end of stream; raises StreamBroken on a drop."""
        # Follow, never poll: a rolled webapp pod is deleted seconds after it
        # terminates, and only an open stream delivers its last lines.
        params = {'follow': 'true', 'timestamps': 'true', 'container': container}
        if since_time:
            params['sinceTime'] = since_time
        resp = self._get(f'pods/{pod}/log', params, stream=True, read_timeout=STREAM_READ_TIMEOUT)
        try:
            # Split on newline alone: the default also splits a bare \r inside a line.
            for raw in resp.iter_lines(delimiter=b'\n'):
                if raw:   # an explicit delimiter yields b'' at each chunk's end
                    stamp, _, line = raw.decode('utf-8', errors='replace').partition(' ')
                    yield stamp, line
        except (requests.RequestException, OSError) as exc:
            raise StreamBroken(f'{pod}/{container}: {exc}') from exc
        finally:
            resp.close()
