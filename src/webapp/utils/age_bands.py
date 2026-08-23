"""Age-band ladders: the shared vocabulary behind the age-range filter controls.

A **ladder** is an ordered ``((label, upper_days), ...)`` tuple where ``upper_days``
is a *cumulative* age threshold and the final band's is ``None`` (open-ended). Band
*i* therefore covers ages ``[lower, upper)`` days, where ``lower`` is band *i-1*'s
``upper``. This is the shape of the fs-scans plugin's ``ATIME_BUCKETS``, which is
where the idea comes from and which remains the single source of truth for the disk
surfaces.

Two directions are needed, and having both here is the point:

* :func:`band_bounds` — a *span* of bands -> the two date strings that select it.
  ``_atime_band_bounds`` in ``webapp/disk_scans/service.py`` has always done this for
  a single band; a range control needs the same thing for ``lo..hi``.
* :func:`bands_for` — the two date strings -> the span, or ``None`` when they don't
  land on band edges. This is what lets a server-rendered control put its thumbs
  where the current filter actually is, and fall back to a "custom" state when the
  filter was typed rather than picked.

WARNING: **Ages count back from an anchor, not from today.** The disk surfaces anchor on
``reference_scan_date`` — the newest scan — because a file's age is measured from
when it was *observed*, and a scan can be days old. Passing ``date.today()`` there
would silently shift every band. Time-anchored surfaces (jobs) do anchor on today.

WARNING: **The bounds are half-open by construction, and the consumers rely on it.**
``before`` is the newer edge and ``after`` the older one. Dates *decrease* as the band
index rises — a later band is an older file — so band *i*'s ``after`` is deliberately
equal to band *i+1*'s ``before``. The fs-scans query builder compares strictly on both
sides (``max_atime > :accessed_after``, ``max_atime < :accessed_before``), so exactly
one band claims any given instant and the access-history drill-downs partition the
total. Anything that makes either bound inclusive has to re-derive the edges here, or
adjacent bands will double-count a whole boundary day — which is visible only as
drill-downs summing to more than their parent. :func:`tiles_without_overlap` is the
guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence, Tuple

#: Ladders are ``((label, cumulative_upper_days | None), ...)``.
Ladder = Sequence[Tuple[str, Optional[int]]]

_DATE_FMT = '%Y-%m-%d'


def thresholds(ladder: Ladder) -> list[Tuple[str, int, Optional[int]]]:
    """Expand a ladder to ``[(label, lower_days, upper_days), ...]``.

    ``lower`` is the previous band's ``upper`` (0 for the first), so the bands are
    contiguous by construction rather than by the caller keeping two lists in step.
    """
    out: list[Tuple[str, int, Optional[int]]] = []
    prev = 0
    for label, upper in ladder:
        out.append((label, prev, upper))
        if upper is not None:
            prev = upper
    return out


def labels(ladder: Ladder) -> list[str]:
    """The ladder's labels, in order — the display vocabulary of the control."""
    return [label for label, _upper in ladder]


def band_bounds(ladder: Ladder, anchor: datetime,
                lo: int = 0, hi: Optional[int] = None) -> Tuple[Optional[str], str]:
    """Date strings selecting the span of bands ``lo..hi`` inclusive.

    Returns ``(after, before)`` as ``YYYY-MM-DD``, where *after* is the **older**
    edge (``None`` when the span reaches the open-ended oldest band) and *before* is
    the **newer** edge. Note the inversion: a *later* band index is an *older* file,
    so the span's ``after`` comes from ``hi`` and its ``before`` from ``lo``.

    ``lo == hi`` reproduces the single-band mapping the access-history drill-down has
    always used.
    """
    rows = thresholds(ladder)
    if hi is None:
        hi = len(rows) - 1
    lo = max(0, min(lo, len(rows) - 1))
    hi = max(lo, min(hi, len(rows) - 1))

    _, lower, _ = rows[lo]
    _, _, upper = rows[hi]
    before = (anchor - timedelta(days=lower)).strftime(_DATE_FMT)
    after = None if upper is None else (anchor - timedelta(days=upper)).strftime(_DATE_FMT)
    return after, before


def bands_for(ladder: Ladder, anchor: datetime,
              after: Optional[str], before: Optional[str]) -> Optional[Tuple[int, int]]:
    """The inverse of :func:`band_bounds`: dates -> ``(lo, hi)``, or ``None``.

    ``None`` means the pair does not describe a whole number of bands — a hand-typed
    range, or a half-open one — and the caller should render its "custom" state
    rather than snap the control to a span that isn't what the filter says.

    An absent *before* means "up to the anchor" (band 0's newer edge); an absent
    *after* means the span runs to the open-ended oldest band.
    """
    rows = thresholds(ladder)
    if not rows:
        return None

    lo = hi = None
    for i, (_label, lower, upper) in enumerate(rows):
        if lo is None:
            edge = (anchor - timedelta(days=lower)).strftime(_DATE_FMT)
            if (before or edge) == edge:
                lo = i
        if upper is None:
            if after is None:
                hi = i
        elif after == (anchor - timedelta(days=upper)).strftime(_DATE_FMT):
            hi = i

    if lo is None or hi is None or lo > hi:
        return None
    return lo, hi


def band_map(ladder: Ladder, anchor: datetime,
             after_key: str, before_key: str) -> list[dict]:
    """Per-band ``{'label', after_key, before_key}`` rows, in ladder order.

    This is what a template hands to the browser as a JSON data block: with the whole
    ladder pre-resolved to dates, the client only has to *index* it, so no date
    arithmetic — and therefore no timezone or DST reasoning — ever happens in
    JavaScript.
    """
    rows = []
    for i, label in enumerate(labels(ladder)):
        after, before = band_bounds(ladder, anchor, i, i)
        rows.append({'label': label, after_key: after, before_key: before})
    return rows


def tiles_without_overlap(ladder: Ladder, anchor: datetime) -> bool:
    """Do the single-band bounds tile the axis with no gap and no overlap?

    True for any well-formed ladder under the half-open convention described in the
    module docstring. Exists to be asserted: it is the tripwire for a future change
    to either bound's inclusivity, which would otherwise make adjacent bands
    double-count a boundary day and be visible only as drill-downs summing to more
    than their total.
    """
    rows = thresholds(ladder)
    for i in range(len(rows) - 1):
        after_i, _before_i = band_bounds(ladder, anchor, i, i)
        _after_next, before_next = band_bounds(ladder, anchor, i + 1, i + 1)
        # A later band index is an OLDER file, so dates DECREASE down the
        # ladder: band i's older edge is band i+1's newer edge.
        if after_i != before_next:
            return False
    return True


def atime_ladder() -> Optional[Ladder]:
    """The fs-scans access-time ladder, or ``None`` when the plugin is absent.

    Imported lazily and defensively for the same reason the rest of the disk-scans
    service is: fs-scans is an optional plugin, and every surface that touches it
    must degrade rather than 500.
    """
    try:
        from fs_scans.core.models import ATIME_BUCKETS
    except Exception:
        return None
    return ATIME_BUCKETS
