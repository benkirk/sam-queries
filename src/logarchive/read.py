"""Reading the archive back: a time-ordered merge across pods and file kinds."""

from __future__ import annotations

import gzip
import heapq
import re
import time
from pathlib import Path
from typing import Iterator

from logarchive import store

_DURATION = re.compile(r'(\d+)([smhdw])$')
_UNIT = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
#: Node and archiver clocks may disagree slightly; never skip a file on a close call.
MTIME_SLACK = 60


def parse_when(text: str, now: float | None = None) -> float:
    """`48h` (ago) or an RFC3339 timestamp, as epoch seconds."""
    m = _DURATION.match(text)
    if m:
        return (time.time() if now is None else now) - int(m.group(1)) * _UNIT[m.group(2)]
    seconds, nanos = store.parse_ts(text)
    return seconds + nanos / 1e9


def _key(when: float | None) -> store.Key | None:
    return None if when is None else (int(when // 1), int(when % 1 * 1e9))


def _open(path: Path):
    """A text handle on a `.log` or `.log.gz`; a `.log` finalized meanwhile reads from its twin."""
    text = {'encoding': 'utf-8', 'errors': 'replace', 'newline': '\n'}   # a bare \r is not a line end
    try:
        if path.name.endswith(store.DONE):
            return gzip.open(path, 'rt', **text)
        return open(path, **text)
    except FileNotFoundError:
        twin = store.finished_path(path)
        if path.name.endswith(store.DONE) or not twin.exists():
            return None
        return gzip.open(twin, 'rt', **text)


def _records(path: Path, since, until) -> Iterator[tuple[store.Key, str, str]]:
    fh = _open(path)
    if fh is None:
        return
    with fh:
        for raw in fh:
            stamp, _, line = raw.rstrip('\n').partition(' ')
            try:
                key = store.parse_ts(stamp)
            except ValueError:
                continue
            if since is not None and key < since:
                continue
            if until is not None and key > until:
                return
            yield key, stamp, line


def _first_key(path: Path) -> store.Key | None:
    return next((key for key, _, _ in _records(path, None, None)), None)


def _candidates(root: Path, apps, since: float | None) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    apps = list(apps) if apps else sorted(p.name for p in root.iterdir() if p.is_dir())
    paths = []
    for app in apps:
        done = {p.name[:-3] for p in (root / app).glob(f'*{store.DONE}')}
        live = [p for p in (root / app).glob(f'*{store.LIVE}') if p.name not in done]
        paths += live + sorted((root / app).glob(f'*{store.DONE}'))
    if since is None:
        return paths
    kept = []
    for path in paths:
        try:
            if path.stat().st_mtime >= since - MTIME_SLACK:
                kept.append(path)
        except FileNotFoundError:
            kept.append(path)
    return kept


def iter_lines(root: Path, apps=None, since: float | None = None, until: float | None = None
               ) -> Iterator[tuple[str, str]]:
    """Yield (timestamp, line) across every file overlapping the window, in time order."""
    lo, hi = _key(since), _key(until)
    pending = []
    for index, path in enumerate(_candidates(root, apps, since)):
        first = _first_key(path)
        if first is not None and (hi is None or first <= hi):
            pending.append((first, index, path))
    pending.sort()
    # A file is opened only when the merge reaches its first line: the tasks
    # CronJob leaves 96 files a day, far more than one process may hold open.
    heap, nxt = [], 0

    def push(index, records):
        record = next(records, None)
        if record is not None:
            heapq.heappush(heap, (record[0], index, record[1], record[2], records))

    while heap or nxt < len(pending):
        while nxt < len(pending) and (not heap or pending[nxt][0] <= heap[0][0]):
            _, index, path = pending[nxt]
            nxt += 1
            push(index, _records(path, lo, hi))
        if not heap:
            continue
        _, index, stamp, line, records = heapq.heappop(heap)
        yield stamp, line
        push(index, records)
