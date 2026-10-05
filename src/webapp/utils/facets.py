"""In-memory facet chips: self-excluding counts over a list of row dicts.

One engine for the cards whose rows are assembled in Python (account requests
and the XRAS worklists). A dimension's strip counts the rows the OTHER
dimensions keep, so picking a chip never zeroes its own row. Values within a
dimension are ORed; dimensions are ANDed. SQL-backed logs use
``querykit.faceted`` with ``faceted_log.build_facet_strip`` instead.
"""

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Union

from webapp.utils.htmx import read_multi


@dataclass(frozen=True)
class Facet:
    """One chip dimension; ``name`` is the query param and the selection key."""

    name: str
    #: Row key, or a callable returning one value or a collection of them.
    key: Union[str, Callable, None] = None
    #: Declared vocabulary in display order; ``None`` means the observed values.
    order: Optional[Sequence[str]] = None
    labels: Optional[Mapping[str, str]] = None
    icons: Optional[Mapping[str, str]] = None
    #: Drop a zero-count value unless it is selected.
    hide_zero: bool = False
    #: Observed values only: most rows first instead of alphabetical.
    by_count: bool = False
    #: Cap on the chips drawn (never on the rows); selected values always stay.
    limit: Optional[int] = None
    #: False for a one-value dimension, whose form control is a single input.
    multi: bool = True

    def values(self, row):
        """The row's values on this dimension; falsy ones cannot be a chip."""
        raw = self.key(row) if callable(self.key) else row.get(self.key or self.name)
        if isinstance(raw, (list, tuple, set, frozenset)):
            return [v for v in raw if v]
        return [raw] if raw else []


class FacetSet:
    """An ordered set of :class:`Facet` dimensions over one row list."""

    def __init__(self, *facets):
        self._facets = {f.name: f for f in facets}

    def __iter__(self):
        return iter(self._facets.values())

    @property
    def names(self):
        return tuple(self._facets)

    def read(self, args):
        """``{name: [values]}`` off a request args mapping, every name present."""
        selected = {}
        for facet in self:
            values = read_multi(args, facet.name)
            selected[facet.name] = values if facet.multi else values[-1:]
        return selected

    def apply(self, rows, selected, skip=None):
        """The rows every dimension keeps, leaving ``skip`` out for its own strip."""
        out = list(rows)
        for facet in self:
            wanted = set(selected.get(facet.name) or ())
            if facet.name == skip or not wanted:
                continue
            out = [r for r in out if wanted.intersection(facet.values(r))]
        return out

    def strip(self, rows, selected, name):
        """One dimension's chips: ``[{'value', 'label', 'count'[, 'icon']}]``."""
        facet = self._facets[name]
        chosen = list(selected.get(name) or ())
        counts = {}
        for row in self.apply(rows, selected, skip=name):
            for value in set(facet.values(row)):
                counts[value] = counts.get(value, 0) + 1

        seen = {v for row in rows for v in facet.values(row)} | set(chosen)
        if facet.order is not None:
            values = list(facet.order)
            values += sorted(seen - set(values))
        elif facet.by_count:
            values = sorted(seen, key=lambda v: (-counts.get(v, 0), v))
        else:
            values = sorted(seen)
        if facet.hide_zero:
            values = [v for v in values if counts.get(v) or v in chosen]
        if facet.limit is not None:
            values = values[:facet.limit] + [
                v for v in values[facet.limit:] if v in chosen]

        labels, icons = facet.labels or {}, facet.icons or {}
        chips = []
        for value in values:
            chip = {'value': value, 'label': labels.get(value, value),
                    'count': counts.get(value, 0)}
            if value in icons:
                chip['icon'] = icons[value]
            chips.append(chip)
        return chips

    def strips(self, rows, selected):
        """Every dimension's chips, keyed by name."""
        return {name: self.strip(rows, selected, name) for name in self.names}
