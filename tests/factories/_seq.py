"""Process-local sequence counters for factory-generated identifiers.

Each xdist worker is its own process, so the counters here are per-worker.
**But** each worker still writes to the same shared mysql-test database,
and SAVEPOINT isolation does not protect against UNIQUE constraint
collisions while two workers are concurrently holding write locks on the
same identifier. So the worker ID is baked into every generated value to
keep namespaces disjoint across workers.
"""
import datetime as _dt
import os
from collections import defaultdict
from itertools import count
from typing import Optional

# 'gw0', 'gw1', … under xdist; '' when running serially. We strip the 'gw'
# prefix to keep generated identifiers short (usernames are capped at 35
# chars and acronyms at 15).
_WORKER_TAG = os.environ.get("PYTEST_XDIST_WORKER", "").removeprefix("gw") or "0"

_counters: dict[str, "count[int]"] = defaultdict(lambda: count(1))


def next_seq(prefix: str) -> str:
    """Return the next worker-namespaced identifier for `prefix`.

    Format: `<prefix><worker><counter>`, e.g. `usr00001`, `usr10001`, …
    on workers 0 and 1 respectively.
    """
    return f"{prefix}{_WORKER_TAG}{next(_counters[prefix]):04d}"


def next_int(prefix: str) -> int:
    """Return the next raw integer for `prefix` — useful for unix_uid etc."""
    return next(_counters[prefix])


def reset_seq(prefix: Optional[str] = None) -> None:
    """Reset all sequences (prefix=None) or just one. Tests rarely need this."""
    if prefix is None:
        _counters.clear()
    else:
        _counters.pop(prefix, None)


# Worker-namespaced date generator: each xdist worker gets a disjoint
# slice of dates so concurrent workers don't collide on PKs of small
# date-keyed parent tables (e.g. *_charge_summary_status). Each worker
# slice spans 365 days; counters within a slice walk forward.
_DATE_EPOCH = _dt.date(2090, 1, 1)
_DATE_SLICE_DAYS = 365


def next_date(prefix: str = "date") -> _dt.date:
    """Return a worker-unique date that walks forward on each call.

    Use for any test that needs a date-typed PK or FK target where
    parallel workers must not collide. Stays within a worker-specific
    365-day slice; wraps to slice start after 365 calls (more than
    enough for any single test run).
    """
    worker_offset = int(_WORKER_TAG) * _DATE_SLICE_DAYS
    counter_offset = next_int(prefix) % _DATE_SLICE_DAYS
    return _DATE_EPOCH + _dt.timedelta(days=worker_offset + counter_offset)
