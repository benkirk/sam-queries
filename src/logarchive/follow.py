"""The archiver loop: one follower thread per (pod, container), plus housekeeping."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from logarchive import store
from logarchive.kube import KubeError, PodGone, StreamBroken

log = logging.getLogger('logarchive')

POLL_SECONDS = 10
PRUNE_SECONDS = 3600
RECLAIM_SECONDS = 60
WRITE_RETRY_SECONDS = 30
BACKOFF_MAX = 30
#: A stream that lasted this long was healthy: reconnect at once, no backoff.
HEALTHY_STREAM_SECONDS = 5


class Follower:
    """Copies one container's log to its file until the container is finished."""

    def __init__(self, client, root: Path, app: str, pod: str, container: str, stop,
                 clock=time.monotonic):
        self.client, self.app, self.pod, self.container = client, app, pod, container
        self.path = store.log_path(root, app, pod, container)
        self.stop, self.clock = stop, clock
        self._wrote = 0

    def run(self) -> None:
        try:
            self._follow()
        except Exception:
            log.exception('follower %s/%s stopped; the next poll restarts it', self.pod, self.container)

    def _finished(self) -> bool:
        """True when no further line can arrive: the pod is gone or the container is done."""
        try:
            pods = self.client.list_pods(f'app={self.app}')
        except KubeError:
            return False
        pod = next((p for p in pods if p.name == self.pod), None)
        ref = pod.container(self.container) if pod else None
        return ref is None or ref.finished

    def _follow(self) -> None:
        backoff = 1
        while not self.stop.is_set():
            # Read before the stream opens: only a stream begun on a finished
            # container, and read to a clean end, has delivered its last line.
            finished = self._finished()
            store.repair(self.path)
            cursor = store.cursor(self.path)
            began, self._wrote = self.clock(), 0
            try:
                self._copy(cursor)
            except PodGone:
                # A .log.gz is final, so one 404 is not enough: the listing must agree.
                if self._finished():
                    return self._finalize()
            except StreamBroken:
                pass   # a quiet pod's read timeout, or a dropped connection
            except KubeError as exc:
                log.warning('%s/%s: %s', self.pod, self.container, exc)
            else:
                if finished:
                    return self._finalize()
            if self._wrote or self.clock() - began >= HEALTHY_STREAM_SECONDS:
                backoff = 1
                continue
            self.stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _finalize(self) -> None:
        try:
            store.finalize(self.path)
        except OSError as exc:   # a full volume: the next poll starts a follower that retries
            log.warning('finalize %s: %s', self.path.name, exc)

    def _copy(self, cursor) -> None:
        key_at, skip = cursor if cursor else (None, 0)
        # sinceTime has second granularity, so a resume re-delivers the cursor's
        # second: drop older lines, and the first `skip` lines equal to the cursor.
        since = store.since_time(key_at) if key_at else None
        for stamp, line in self.client.stream_log(self.pod, self.container, since):
            if self.stop.is_set():
                raise StreamBroken('shutting down')
            try:
                key = store.parse_ts(stamp)
            except ValueError:
                continue   # the API's own notice (a rotated file), not a log line
            if key_at is not None:
                if key < key_at:
                    continue
                if key == key_at and skip > 0:
                    skip -= 1
                    continue
                if key > key_at:
                    key_at = None
            if not self._append(stamp, line):
                raise StreamBroken('shutting down')
            self._wrote += 1

    def _append(self, stamp: str, line: str) -> bool:
        """Write one line, retrying a full or failing volume; False only on shutdown."""
        warned = False
        while True:
            try:
                store.append(self.path, stamp, line)
                return True
            except OSError as exc:
                if not warned:
                    log.warning('write to %s failed (%s); retrying every %ds',
                                self.path.name, exc, WRITE_RETRY_SECONDS)
                    warned = True
                try:
                    store.repair(self.path)   # a failed write can leave half a line
                except OSError:
                    pass
                if self.stop.wait(WRITE_RETRY_SECONDS):
                    return False


def _spawn(follower: Follower):
    thread = threading.Thread(target=follower.run, daemon=True,
                              name=f'follow-{follower.pod}-{follower.container}')
    thread.start()
    return thread


class Archiver:
    def __init__(self, client, root: Path, apps: list[str], retention_days: float, *,
                 self_pod: str | None = None, poll_seconds: float = POLL_SECONDS,
                 clock=time.monotonic, spawn=_spawn):
        self.client, self.root, self.apps = client, Path(root), list(apps)
        self.retention_days, self.self_pod = retention_days, self_pod
        self.poll_seconds, self.clock, self.spawn = poll_seconds, clock, spawn
        self.stop = threading.Event()
        self.followers: dict[Path, object] = {}
        self._pruned_at = self._reclaimed_at = None

    def run(self, stop=None) -> None:
        self.stop = stop or self.stop
        while not self.stop.is_set():
            # Nothing here may end the process: a crash loop archives nothing.
            try:
                self.poll()
            except Exception:
                log.exception('poll failed')
            try:
                store.beat(self.root)
            except OSError as exc:
                log.warning('heartbeat: %s', exc)
            self.stop.wait(self.poll_seconds)

    def poll(self) -> None:
        self._housekeeping()
        self.followers = {path: h for path, h in self.followers.items() if h.is_alive()}
        for app in self.apps:
            try:
                pods = self.client.list_pods(f'app={app}')
            except KubeError as exc:
                log.warning('list app=%s: %s', app, exc)
                continue
            names = set()
            for pod in pods:
                if pod.name == self.self_pod:
                    continue   # never archive the archiver's own pod
                names.add(pod.name)
                for ref in pod.containers:
                    path = store.log_path(self.root, app, pod.name, ref.name)
                    # A .log.gz is final: that container is never fetched again.
                    if ref.started and not store.is_finished(path) and not self._alive(path):
                        self.followers[path] = self.spawn(
                            Follower(self.client, self.root, app, pod.name, ref.name, self.stop, self.clock))
            self._finalize_orphans(app, names)

    def _alive(self, path: Path) -> bool:
        handle = self.followers.get(path)
        return handle is not None and handle.is_alive()

    def _finalize_orphans(self, app: str, listed: set[str]) -> None:
        """A live file whose pod is gone (deleted while the archiver was down) is finished."""
        for path in sorted((self.root / app).glob(f'*{store.LIVE}')):
            if store.pod_of(path) in listed or self._alive(path):
                continue
            try:
                store.finalize(path)
            except OSError as exc:
                log.warning('finalize %s: %s', path.name, exc)

    def _housekeeping(self) -> None:
        now = self.clock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if self._reclaimed_at is None or now - self._reclaimed_at >= RECLAIM_SECONDS:
                self._reclaimed_at = now
                store.reclaim(self.root)
            if self._pruned_at is None or now - self._pruned_at >= PRUNE_SECONDS:
                self._pruned_at = now
                store.tidy(self.root)
                removed = store.prune(self.root, self.retention_days)
                if removed:
                    log.info('pruned %d file(s) past %s days', len(removed), self.retention_days)
        except OSError as exc:
            log.warning('housekeeping: %s', exc)
