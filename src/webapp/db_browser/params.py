"""The table view's URL state: filters, sort, paging, visible columns, row keys.

Everything a view shows is in its query string, so any view can be bookmarked or
pasted. ``ViewState.to_args`` drops empty filter rows and defaults, and the table
route redirects a plain GET form submission to that canonical form.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from dbbrowse import MAX_FILTERS, RawFilter, url_value
from webapp.utils.htmx import DEFAULT_PER_PAGE, PER_PAGE_CHOICES, read_page, read_sort


@dataclass(frozen=True)
class ViewState:
    filters: Tuple[RawFilter, ...] = ()
    sort: Optional[str] = None
    desc: bool = False
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE
    after: Optional[str] = None
    cols: Tuple[str, ...] = field(default_factory=tuple)   # empty = every column

    @classmethod
    def from_args(cls, args, sortable=()) -> 'ViewState':
        """``sortable`` is the sort whitelist; a sort outside it reads as no sort."""
        filters = []
        for i in range(MAX_FILTERS + 1):
            col = (args.get(f'f{i}.col') or '').strip()
            if col:
                filters.append(RawFilter(col, args.get(f'f{i}.op') or 'eq',
                                         args.get(f'f{i}.v') or ''))
        page = read_page(args)
        sort = read_sort(args, sortable, default_dir='asc')
        return cls(
            filters=tuple(filters),
            sort=sort['sort_by'],
            desc=sort['sort_by'] is not None and sort['sort_dir'] == 'desc',
            page=page['n'],
            per_page=min(PER_PAGE_CHOICES, key=lambda n: abs(n - page['per_page'])),
            after=args.get('after') or None,
            cols=tuple(c for c in args.getlist('cols') if c),
        )

    def with_(self, **changes) -> 'ViewState':
        return replace(self, **changes)

    def to_args(self, all_columns=()) -> Dict[str, object]:
        """The minimal query args for this state; ``url_for(..., **args)`` renders them."""
        args: Dict[str, object] = {}
        for i, f in enumerate(self.filters):
            args[f'f{i}.col'] = f.col
            args[f'f{i}.op'] = f.op
            if f.value:
                args[f'f{i}.v'] = f.value
        if self.sort:
            args['sort_by'] = self.sort
            args['sort_dir'] = 'desc' if self.desc else 'asc'
        if self.after:
            args['after'] = self.after
        elif self.page > 1:
            args['page'] = self.page
        if self.per_page != DEFAULT_PER_PAGE:
            args['per_page'] = self.per_page
        if self.cols and set(self.cols) != set(all_columns):
            args['cols'] = list(self.cols)
        return args

    def matches(self, request_args, all_columns=()) -> bool:
        """True when ``request_args`` already spell this state canonically."""
        got = sorted((k, v) for k, vs in request_args.lists() for v in vs)
        want = sorted((k, str(v)) for k, vals in self.to_args(all_columns).items()
                      for v in (vals if isinstance(vals, list) else [vals]))
        return got == want


def read_key(args) -> Dict[str, str]:
    """``k.<column>=value`` pairs identifying one row."""
    return {k[2:]: v for k, v in args.items() if k.startswith('k.') and len(k) > 2}


def key_args(key: Dict[str, object]) -> Dict[str, str]:
    return {f'k.{col}': url_value(v) for col, v in key.items()}


def eq_filter_args(pairs: List[Tuple[str, object]]) -> Dict[str, str]:
    args = {}
    for i, (col, value) in enumerate(pairs):
        args.update({f'f{i}.col': col, f'f{i}.op': 'eq', f'f{i}.v': url_value(value)})
    return args
