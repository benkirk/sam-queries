"""The count / page / facet trio over a declarative table spec.

Three tables in this codebase render the same shape — a filtered, paginated
log with facet chips above it that double as switchers (``xras_action_log``,
``notification_log``, and now ``task_run``). This module is the third-copy
extraction of the parts that were genuinely identical.

**What is here**: the SQL skeleton — count, page, group-by — plus the
self-exclusion rule that makes facet chips work.

**What is deliberately NOT here**: the per-table ``_filters()`` bodies. Those
are bespoke SQL (``ilike`` across different columns, index-friendly ``IN``
forms) and hiding them behind a DSL would cost more than it saves. Each table
keeps its own and hands it to :class:`LogSpec`.

Imports only SQLAlchemy — no ORM models from any package, no Flask.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LogSpec:
    """Everything the helpers below need to know about one log table.

    Attributes:
        model: the ORM class to select rows of.
        id_column: the primary-key column, used for ``count()`` and as the
            ordering tiebreaker.
        order_columns: the newest-first ordering, as already-directed columns
            (``Model.created.desc()``). ``id_column.desc()`` is appended
            automatically as the tiebreaker, so two rows sharing a timestamp
            cannot swap places between pages.
        dimensions: facet name → column, e.g. ``{'status': Log.status}``.
            **Insertion order is the error-message order**, so declare it the
            way an operator reads the chips.
        owned_filter: facet name → the ``build_filters`` keyword that scopes
            it, e.g. ``{'status': 'statuses'}``. This is what
            :func:`facet_counts` drops to achieve self-exclusion.
        build_filters: the table's own ``_filters()`` — keyword-only, returns
            a list of WHERE terms.
    """

    model: type
    id_column: Any
    order_columns: tuple
    dimensions: Mapping[str, Any]
    owned_filter: Mapping[str, str]
    build_filters: Callable[..., list]

    def __post_init__(self) -> None:
        missing = set(self.dimensions) - set(self.owned_filter)
        if missing:
            raise ValueError(
                f'{sorted(missing)} appear in dimensions but not in '
                f'owned_filter; facet_counts could not self-exclude them')


def count_rows(session: Session, spec: LogSpec, **filters) -> int:
    """Total matching rows, for pagination."""
    return session.execute(
        select(func.count(spec.id_column))
        .where(*spec.build_filters(**filters))
    ).scalar_one()


def page_rows(session: Session, spec: LogSpec, *,
              limit: Optional[int] = 100, offset: int = 0,
              **filters) -> List[Any]:
    """One page of rows, newest first.

    ``limit=None`` means no cap — for a caller that has already bounded the
    result some other way. A paginated table must always pass a number.
    """
    query = (select(spec.model)
             .where(*spec.build_filters(**filters))
             .order_by(*spec.order_columns, spec.id_column.desc()))
    if limit is not None:
        query = query.limit(limit)
    return list(session.execute(query.offset(offset)).scalars())


def facet_counts(session: Session, spec: LogSpec, dimension: str,
                 **filters) -> Dict[str, int]:
    """Counts for one facet dimension, **excluding that dimension's filter**.

    ⚠️ Self-exclusion is the whole point, and the reason this function exists
    once instead of three times. Scope a dimension by itself and every
    unselected value drops to zero the moment one is picked — the chips stop
    being switchers and become dead ends. So asking for the ``status`` facet
    drops the ``statuses`` filter while keeping every other one.

    Returns the observed values only. Zero-filling against a declared
    vocabulary is the caller's job (``webapp.utils.faceted_log
    .build_facet_strip``), because only the caller knows which vocabulary and
    what to do with a value outside it.
    """
    column = spec.dimensions.get(dimension)
    if column is None:
        raise ValueError(
            f'unknown facet dimension {dimension!r}; expected one of '
            f'{", ".join(spec.dimensions)}')

    scoped = {k: v for k, v in filters.items()
              if k != spec.owned_filter[dimension]}

    rows = session.execute(
        select(column, func.count(spec.id_column))
        .where(*spec.build_filters(**scoped))
        .group_by(column)
        .order_by(column)
    ).all()
    return {value: count for value, count in rows}
