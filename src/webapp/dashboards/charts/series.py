"""One shape for the stacked charts' band data.

Four producers hand the stacked charts the same concept under three different
key names, which is why their color and legend loops could never be shared:

    series[i]['label']      sam/queries/charges.py, system_status user_proj_queues
    series[i]['username']   sam/queries/disk_usage.py
    (name, values) tuples   the jobs plugin, via `_jobs_timeseries_series`

Normalizing happens **at the chart boundary**, not in the query layer — those
functions have their own consumers and tests, and changing their envelopes to
suit the renderer would be the tail wagging the dog.

The invariant this buys is the useful part. Eight scattered
``label == 'Others'`` string comparisons and three ``is None`` checks collapse
to one rule:

    an artist is linked iff its `link_key` is not None

so "Others", the unknown bucket and any aggregate row are inert by
construction rather than by remembering to test for them. `Series.is_linkable`
is the single place that rule lives.

**No matplotlib import here, by design** — see the note in `links.py`.
"""

from dataclasses import dataclass
from typing import Sequence

#: The conventional label for an aggregated remainder band. Producers emit it
#: verbatim; it is never linkable and never consumes a palette slot.
OTHERS = 'Others'


@dataclass(frozen=True)
class Series:
    """One band of a stacked chart."""

    label: str
    values: Sequence
    #: Identifier a drill/modal link is built from, or None for an inert band
    #: (an aggregate, or an entity with nothing to link to). Usually equals
    #: `label`; kept separate because the *displayed* label often carries a
    #: formatted suffix — "alice (1,234)" — that must not reach the URL.
    link_key: str | None = None

    @property
    def is_linkable(self) -> bool:
        return self.link_key is not None


def _series(label, values, linkable=True) -> Series:
    label = '' if label is None else str(label)
    is_other = (label == OTHERS)
    return Series(label=label, values=list(values),
                  link_key=(label if (linkable and not is_other and label) else None))


def from_label_series(raw) -> list[Series]:
    """`[{'label': ..., 'values': [...]}, ...]` — charges + user/proj queues."""
    return [_series(s.get('label'), s.get('values') or []) for s in (raw or [])]


def from_username_series(raw) -> list[Series]:
    """`[{'username': ..., 'values': [...]}, ...]` — disk usage."""
    return [_series(s.get('username'), s.get('values') or []) for s in (raw or [])]


def from_pairs(raw) -> list[Series]:
    """`[(name, [values]), ...]` — the jobs timeseries adapter's output."""
    return [_series(name, values) for name, values in (raw or [])]


def assign_colors(series: Sequence[Series], palette, others_color,
                  reverse: bool = False) -> list:
    """One color per band, bottom -> top.

    "Others" takes `others_color` and **does not advance the palette cursor**,
    so a named band keeps its color whether or not a remainder exists.

    `reverse` walks the palette backwards over the named bands. The
    user/proj stacked area needs it and the others must not have it: that
    chart's series arrive as ``[Others, lowest-rank, …, highest-rank]`` so the
    stack reads bottom-to-top by rank. Walking forward would hand the
    *lowest*-rank band the warmest color — backwards from the pace chart's
    convention, where the biggest band is gold. This is a real semantic
    difference between two charts, not a flag someone added for symmetry.
    """
    n_named = sum(1 for s in series if s.label != OTHERS)
    out = []
    named_idx = 0
    for s in series:
        if s.label == OTHERS:
            out.append(others_color)
            continue
        idx = (n_named - 1 - named_idx) if reverse else named_idx
        out.append(palette[idx % len(palette)])
        named_idx += 1
    return out
