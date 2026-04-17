"""Tolerance helpers for legacy-vs-new API comparison.

Lifted from the abandoned tests/integration/test_legacy_api_parity.py.
The leading underscores are dropped so these are importable across the
comparator modules.
"""

from datetime import date


def dates_within_one_day(d1: str, d2: str) -> bool:
    """Return True if two ISO date strings (YYYY-MM-DD) differ by at most one day.

    The legacy Java system rounds allocation end dates to the first day of the
    following month (e.g., "2026-07-01"), while the new API stores the actual
    last day of the allocation period (e.g., "2026-06-30"). A ±1-day difference
    is therefore expected and should not be treated as a mismatch.
    """
    try:
        date1 = date.fromisoformat(d1)
        date2 = date.fromisoformat(d2)
        return abs((date1 - date2).days) <= 1
    except (ValueError, TypeError):
        return d1 == d2


def normalize_gecos(gecos: str) -> str:
    """Strip whitespace from each comma-separated field of a gecos string.

    The legacy system strips field-internal whitespace; the new implementation
    may produce leading/trailing spaces in phone or org fields.
    """
    return ','.join(part.strip() for part in gecos.split(','))


def within_tolerance(a: float, b: float, pct: float = 5.0, abs_floor: float = 0.0) -> bool:
    """Return True if `a` and `b` are within `pct`% of each other.

    If both values are at or below `abs_floor`, treat them as equal regardless
    of relative difference (avoids spurious failures on tiny allocations where
    a few-AU absolute difference is large in percentage terms).
    """
    if a == b == 0:
        return True
    max_val = max(abs(a), abs(b))
    if max_val <= abs_floor:
        return True
    return abs(a - b) / max_val <= pct / 100.0


def count_tolerance(count: int, pct: float = 5.0, floor: int = 10) -> int:
    """Return the allowed difference for a count comparison.

    Returns whichever is larger: `floor` or `pct`% of `count`. Used to
    accommodate DB-mirror sync lag between sam.ucar.edu and the local
    development database.
    """
    return max(floor, int(count * pct / 100.0))


def subset_diff(small: set, large: set, max_missing: int = 10) -> tuple[set, bool]:
    """Return `(missing_items, ok)` where `ok` is True if at most `max_missing`
    elements of `small` are absent from `large`.

    Replaces the assertion-raising `_assert_subset_with_tolerance` from the
    pytest version with a value-returning equivalent.
    """
    missing = small - large
    return missing, len(missing) <= max_missing
