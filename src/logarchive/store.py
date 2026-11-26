"""The files on the archive volume. The archive is its own state: no cursor file.

One file per container, `<root>/<app label>/<pod>__<container>.log`, each line
as the API gave it (RFC3339Nano timestamp, a space, the text). A `.log.gz`
means the container is finished and is never fetched again.
"""

from __future__ import annotations

import calendar
import gzip
import logging
import os
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger('logarchive')

HEARTBEAT = '.heartbeat'
LIVE, DONE, TMP = '.log', '.log.gz', '.log.gz.tmp'

_STAMP = re.compile(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$')

#: (epoch seconds, nanoseconds)
Key = tuple[int, int]


def parse_ts(stamp: str) -> Key:
    """An RFC3339Nano timestamp as a comparable key; raises ValueError."""
    # Never compare the strings: Go trims trailing zeros, so "…:01.5Z" sorts
    # after "…:01.123456789Z".
    m = _STAMP.match(stamp)
    if not m:
        raise ValueError(f'not an RFC3339 timestamp: {stamp[:40]!r}')
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    seconds = calendar.timegm((y, mo, d, h, mi, s))
    zone = m.group(8)
    if zone != 'Z':
        offset = int(zone[1:3]) * 3600 + int(zone[4:6]) * 60
        seconds -= offset if zone[0] == '+' else -offset
    return seconds, int((m.group(7) or '').ljust(9, '0'))


def since_time(key: Key) -> str:
    """The API's `sinceTime` for a cursor: UTC, floored to the second."""
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(key[0]))


def log_path(root: Path, app: str, pod: str, container: str) -> Path:
    return Path(root) / app / f'{pod}__{container}{LIVE}'


def finished_path(path: Path) -> Path:
    return path.with_name(path.name + '.gz')


def is_finished(path: Path) -> bool:
    return finished_path(path).exists()


def pod_of(path: Path) -> str:
    return path.name.split('__', 1)[0]


def repair(path: Path) -> None:
    """Cut a torn final line back to the last newline; the stream re-delivers it."""
    try:
        with open(path, 'rb+') as fh:
            size = fh.seek(0, os.SEEK_END)
            pos = size
            while pos > 0:
                start = max(0, pos - 65536)
                fh.seek(start)
                chunk = fh.read(pos - start)
                cut = chunk.rfind(b'\n')
                if cut >= 0:
                    pos = start + cut + 1
                    break
                pos = start
            if pos < size:
                fh.truncate(pos)
    except FileNotFoundError:
        pass


def cursor(path: Path) -> tuple[Key, int] | None:
    """(last timestamp, lines at that timestamp), read from the file's tail."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    want = 65536
    while True:
        start = max(0, size - want)
        with open(path, 'rb') as fh:
            fh.seek(start)
            lines = fh.read(size - start).split(b'\n')[:-1]
        if start > 0:
            lines = lines[1:]   # the first line of a mid-file read is partial
        keys = []
        for raw in reversed(lines):
            try:
                keys.append(parse_ts(raw.split(b' ', 1)[0].decode('ascii', errors='replace')))
            except ValueError:
                break
            if keys[-1] != keys[0]:
                break
        if not keys:
            if start == 0:
                return None
        elif keys[-1] != keys[0] or len(keys) < len(lines) or start == 0:
            return keys[0], sum(1 for k in keys if k == keys[0])
        want *= 2


def append(path: Path, timestamp: str, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'ab') as fh:
        fh.write(f'{timestamp} {line}\n'.encode('utf-8', errors='replace'))


def finalize(path: Path) -> Path:
    """Gzip a finished container's file; an empty marker if it never logged."""
    done = finished_path(path)
    if not done.exists():
        tmp = path.with_name(path.name + '.gz.tmp')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with gzip.open(tmp, 'wb') as out:
                try:
                    with open(path, 'rb') as src:
                        shutil.copyfileobj(src, out)
                    stat = path.stat()
                except FileNotFoundError:
                    stat = None
            if stat:   # keep the last write time: retention and `cat` read mtime
                os.utime(tmp, (stat.st_atime, stat.st_mtime))
            os.replace(tmp, done)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
    path.unlink(missing_ok=True)
    return done


def _files(root: Path, suffix: str) -> list[Path]:
    return sorted(Path(root).glob(f'*/*{suffix}'))


def tidy(root: Path) -> None:
    """Remove what a kill mid-finalize leaves: a temp file, or a plain twin of a `.log.gz`."""
    for tmp in _files(root, TMP):
        tmp.unlink(missing_ok=True)
    for done in _files(root, DONE):
        done.with_name(done.name[:-3]).unlink(missing_ok=True)


def prune(root: Path, retention_days: float, now: float | None = None) -> list[Path]:
    """Delete finished files past retention. A live `.log` is never pruned."""
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = []
    for done in _files(root, DONE):
        if done.stat().st_mtime < cutoff:
            done.unlink(missing_ok=True)
            removed.append(done)
    return removed


def volume(root: Path) -> tuple[int, int]:
    """(bytes usable in total, bytes free), as `df` counts them."""
    st = os.statvfs(root)
    used = st.f_blocks - st.f_bfree
    return (used + st.f_bavail) * st.f_frsize, st.f_bavail * st.f_frsize


def percent_full(root: Path) -> float:
    total, free = volume(root)
    return 100.0 * (total - free) / total if total else 0.0


def reclaim(root: Path, high: float = 90.0, low: float = 80.0) -> list[Path]:
    """At `high` percent full, delete the oldest finished files until under `low`."""
    before = percent_full(root)
    if before < high:
        return []
    removed = []
    for done in sorted(_files(root, DONE), key=lambda p: p.stat().st_mtime):
        done.unlink(missing_ok=True)
        removed.append(done)
        if percent_full(root) < low:
            break
    log.warning('volume %.0f%% full: pruned %d finished file(s) ahead of retention, now %.0f%%',
                before, len(removed), percent_full(root))
    return removed


def beat(root: Path) -> None:
    path = Path(root) / HEARTBEAT
    path.touch()
    os.utime(path)


def heartbeat_age(root: Path, now: float | None = None) -> float | None:
    try:
        mtime = (Path(root) / HEARTBEAT).stat().st_mtime
    except FileNotFoundError:
        return None
    return max(0.0, (time.time() if now is None else now) - mtime)


def status(root: Path, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    count = size = 0
    newest = oldest = None
    live, largest = [], None
    for suffix in (LIVE, DONE):
        for path in _files(root, suffix):
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            count += 1
            size += st.st_size
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
            oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            if suffix == LIVE:
                name = f'{path.parent.name}/{path.name}'
                live.append(name)
                if largest is None or st.st_size > largest['bytes']:
                    largest = {'name': name, 'bytes': st.st_size}
    total, free = volume(root)
    age = heartbeat_age(root, now)
    return {
        'root': str(root),
        'heartbeat_age': None if age is None else round(age, 1),
        'newest_write_age': None if newest is None else round(max(0.0, now - newest), 1),
        'oldest': None if oldest is None else time.strftime('%Y-%m-%d', time.gmtime(oldest)),
        'files': count,
        'bytes': size,
        'volume_bytes': total,
        'volume_free': free,
        'percent_full': round(100.0 * (total - free) / total, 1) if total else 0.0,
        'live_files': live,
        'largest_live': largest,
    }
