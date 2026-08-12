"""Value ladders: the numeric counterpart to :mod:`webapp.utils.age_bands`.

A **ladder** is an ordered ``((label, lo, hi), ...)`` tuple where the last band's
``hi`` is ``None`` (open-ended). This is the shape of both plugin vocabularies
the filter controls draw on — fs-scans' ``SIZE_BUCKETS`` and job-history's
``*_HIST_BUCKETS`` — which is why one module serves both.

Two directions are needed, and having both here is the point:

* :func:`span_bounds` — a *span* of bands → the two values that select it.
* :func:`span_for` — the two values → the span, or ``None`` when they don't land
  on band edges. This is what lets a server-rendered control put its thumbs where
  the current filter actually is, and fall back to a "custom" state when the
  filter was typed rather than picked.

⚠️ **Inclusivity is the source's business, not this module's.** fs-scans'
``SIZE_BUCKETS`` are half-open — ``1024`` is both band 0's ``hi`` and band 1's
``lo`` — while job-history's histogram tables are inclusive, with band 0 ending
at ``1023``. Nothing here adds, subtracts or compares against the *interior* of a
band: edges are passed through verbatim in both directions. That is what keeps
one module honest for both, and it is not an accident — it is the same
pass-through the existing band drill-downs already rely on
(``_size_band_bounds``, ``_bucket_drill_url``), so a slider position and the
equivalent chart-bar click produce byte-identical filters.

⚠️ **A ladder floor of 0 is a real bound, not a missing one.** ``span_for(l, 0,
…)`` must return band 0, and the control must submit ``0`` rather than an empty
string — see the ``|| ''`` note in ``static/js/actions.js``. Unlike the age
ladders, whose values are date strings, every falsy check here is a bug waiting
to happen.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

#: Ladders are ``((label, lo, hi | None), ...)``.
Ladder = Sequence[Tuple[str, Optional[int], Optional[int]]]


def labels(ladder: Ladder) -> list[str]:
    """The ladder's labels, in order — the display vocabulary of the control."""
    return [label for label, _lo, _hi in ladder]


def span_bounds(ladder: Ladder, lo: int = 0,
                hi: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
    """The ``(lo_value, hi_value)`` selecting the span of bands ``lo..hi``.

    ``hi_value`` is ``None`` when the span reaches the open-ended top band —
    "no upper bound", which callers drop from the query string rather than
    submitting as a number.

    Indices are clamped rather than validated: they arrive from a range input a
    viewer can hand-edit, and the house convention for viewer-editable input is
    to degrade, not to 400.
    """
    rows = list(ladder)
    if not rows:
        return None, None
    if hi is None:
        hi = len(rows) - 1
    lo = max(0, min(lo, len(rows) - 1))
    hi = max(lo, min(hi, len(rows) - 1))
    return rows[lo][1], rows[hi][2]


def span_for(ladder: Ladder, lo_value: Optional[int],
             hi_value: Optional[int]) -> Optional[Tuple[int, int]]:
    """The inverse of :func:`span_bounds`: values → ``(lo, hi)``, or ``None``.

    ``None`` means the pair does not describe a whole number of bands — a
    hand-typed range — and the caller should render its "custom" state rather
    than snap the control to a span that isn't what the filter says.

    An absent *lo_value* means "from the bottom" (band 0); an absent *hi_value*
    means the span runs to the open-ended top band. Note that absent is not the
    same as ``0``: a ladder whose floor is ``0`` has ``0`` as a genuine edge, and
    both spellings correctly resolve to band 0 only because band 0's ``lo`` *is*
    ``0`` — they are not interchangeable in general.
    """
    rows = list(ladder)
    if not rows:
        return None

    lo_i = hi_i = None
    for i, (_label, lo, hi) in enumerate(rows):
        if lo_value is None:
            if i == 0:
                lo_i = 0
        elif lo == lo_value:
            lo_i = i
        if hi_value is None:
            # Only the top band is open-ended, so this matches exactly one row.
            if hi is None:
                hi_i = i
        elif hi == hi_value:
            hi_i = i

    if lo_i is None or hi_i is None or lo_i > hi_i:
        return None
    return lo_i, hi_i


def band_map(ladder: Ladder, lo_key: str, hi_key: str) -> list[dict]:
    """Per-band ``{'label', lo_key, hi_key}`` rows, in ladder order.

    This is what a template hands to the browser as a JSON data block: with the
    whole ladder pre-resolved, the client only has to *index* it. The keys are
    the caller's form-field names, because the handler in ``actions.js`` looks up
    each band value by the field's own ``name`` — which is what lets one handler
    serve every vocabulary with no dimension→field table in the JavaScript.
    """
    return [{'label': label, lo_key: lo, hi_key: hi}
            for label, lo, hi in ladder]


def size_ladder() -> Optional[Ladder]:
    """The fs-scans file-size ladder, or ``None`` when the plugin is absent.

    Imported lazily and defensively for the same reason the rest of the
    disk-scans service is: fs-scans is an optional plugin, and every surface that
    touches it must degrade rather than 500.
    """
    try:
        from fs_scans.core.models import SIZE_BUCKETS
    except Exception:
        return None
    return SIZE_BUCKETS


def machine_ladder(machine: Optional[str], dimension: str) -> Optional[Ladder]:
    """A job-history histogram ladder, right-sized for *machine*.

    ``None`` when the plugin is absent or too old to publish the accessor, and
    on an unknown dimension — every caller falls back to a bare min/max pair, so
    a version skew degrades the control rather than breaking the panel.

    The accessor is the plugin's own (``histogram_buckets``, added for exactly
    this), and it is what ``jobs_histogram`` itself uses to pick its axis. That
    is the guarantee worth having: a band offered by the filter control is a band
    the chart actually draws, so picking one here and clicking the equivalent bar
    there select the same rows.
    """
    try:
        from job_history import histogram_buckets
    except Exception:
        return None
    try:
        return histogram_buckets(dimension, machine)
    except ValueError:
        return None
