"""Dormancy review: SAM users joined to the ``user_last_seen`` ledger by username.

Pure functions over the two sides' already-loaded data, so the two databases
never meet in SQL. The inputs are ``get_user_directory`` (SAM) and
``system_status.queries.last_seen.get_last_seen_by_user`` (status). Ledger
times are naive UTC, so ``now`` must be ``utcnow_naive()``.
Not exported from ``sam.queries``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional

NEVER = 'never'
CURRENT = 'current'
RECENT = 'recent'

#: A sighting this fresh is "now": the user was present in the latest collector
#: snapshot (every 5 min; two ticks survive one missed run). The webapp row is a real
#: event but shares the window so it does not float above the snapshot rows.
CURRENT_WINDOWS = {
    'webapp': timedelta(minutes=10),
    'login': timedelta(minutes=10),
    'pbs': timedelta(minutes=10),
    'jupyterhub': timedelta(minutes=10),
}


def is_current(last_seen: Optional[datetime], kind: Optional[str], now: datetime) -> bool:
    if last_seen is None or kind not in CURRENT_WINDOWS:
        return False
    return now - last_seen <= CURRENT_WINDOWS[kind]


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    lo_days: Optional[int]
    hi_days: Optional[int]


#: Ordered, adjacent, half-open ``[lo, hi)`` day ranges; ``current`` is the per-kind
#: window above and ``never`` has no ledger row.
BUCKETS = (
    Bucket(CURRENT, 'Now', None, None),
    Bucket(RECENT, '< 30 days', 0, 30),
    Bucket('year', '30 days – 1 year', 30, 365),
    Bucket('dormant', '1 – 3 years', 365, 3 * 365),
    Bucket('stale', '> 3 years', 3 * 365, None),
    Bucket(NEVER, 'Never seen', None, None),
)
BUCKET_KEYS = tuple(b.key for b in BUCKETS)

#: A ledger username with no SAM ``users`` row (a service account, a typo'd collector name).
NOT_IN_SAM = 'not in SAM'


@dataclass
class ReviewRow:
    username: str
    name: str
    status: str                     # active | locked | inactive | NOT_IN_SAM
    last_seen: Optional[datetime]
    kind: Optional[str]
    system: Optional[str]
    age: Optional[timedelta]
    bucket: str
    current: bool = False


def bucket_for(last_seen: Optional[datetime], now: datetime, kind: Optional[str] = None) -> str:
    if last_seen is None:
        return NEVER
    if is_current(last_seen, kind, now):
        return CURRENT
    days = (now - last_seen).days
    for b in BUCKETS:
        if b.lo_days is not None and days >= b.lo_days and (b.hi_days is None or days < b.hi_days):
            return b.key
    return RECENT                   # a future timestamp is as recent as it gets


def _newest(per_kind: Mapping[str, tuple], kind: Optional[str]):
    """``(last_seen, kind, system)`` over one kind or all of them, or ``None``."""
    items = [(v[0], k, v[1]) for k, v in per_kind.items() if kind is None or k == kind]
    return max(items) if items else None


def _status(active: bool, locked: bool) -> str:
    return 'locked' if locked else ('active' if active else 'inactive')


def _matches(row: ReviewRow, needle: str) -> bool:
    return needle in row.username.lower() or needle in row.name.lower()


def review(directory: Mapping[str, tuple], ledger: Mapping[str, Mapping[str, tuple]], *,
           now: datetime, kind: Optional[str] = None, bucket: Optional[str] = None,
           search: Optional[str] = None, include_unlisted: bool = False,
           sort_by: str = 'last_seen', sort_dir: str = 'desc'):
    """Filter, count and sort; returns ``(rows, bucket_counts, kind_counts)``.

    ``bucket_counts`` honor kind and search but not the bucket; ``kind_counts``
    honor bucket and search but not the kind, each user bucketed on that kind
    alone. With no bucket chosen, a kind counts the users ever seen on it.
    """
    people = {u: (name, _status(active, locked)) for u, (name, active, locked) in directory.items()}
    if include_unlisted:
        for username in ledger.keys() - people.keys():
            people[username] = ('', NOT_IN_SAM)

    needle = (search or '').strip().lower()
    candidates = []
    for username, (name, status) in people.items():
        row = ReviewRow(username, name, status, None, None, None, None, NEVER)
        if needle and not _matches(row, needle):
            continue
        candidates.append(row)

    kinds = sorted({k for per_kind in ledger.values() for k in per_kind})
    kind_counts = Counter()
    for row in candidates:
        per_kind = ledger.get(row.username, {})
        for k in kinds:
            newest = _newest(per_kind, k)
            b = bucket_for(newest[0] if newest else None, now, k)
            if (bucket is None and newest) or (bucket is not None and b == bucket):
                kind_counts[k] += 1

    bucket_counts = Counter()
    rows = []
    for row in candidates:
        newest = _newest(ledger.get(row.username, {}), kind)
        if newest:
            row.last_seen, row.kind, row.system = newest
            row.age = now - row.last_seen
        row.bucket = bucket_for(row.last_seen, now, row.kind)
        row.current = row.bucket == CURRENT
        bucket_counts[row.bucket] += 1
        if bucket is None or row.bucket == bucket:
            rows.append(row)

    _sort(rows, sort_by, sort_dir)
    return rows, bucket_counts, kind_counts


def _sort(rows: list, sort_by: str, sort_dir: str) -> None:
    reverse = sort_dir == 'desc'
    if sort_by == 'username':
        rows.sort(key=lambda r: r.username, reverse=reverse)
    elif sort_by == 'name':
        rows.sort(key=lambda r: (r.name.lower(), r.username), reverse=reverse)
    else:
        # Never-seen users count as the oldest. Two stable passes keep ties by username ascending.
        rows.sort(key=lambda r: r.username)
        rows.sort(key=lambda r: (r.last_seen is not None, r.last_seen or datetime.min),
                  reverse=reverse)
