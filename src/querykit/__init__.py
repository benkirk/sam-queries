"""Model-agnostic read helpers for faceted, paginated log tables.

A peer package of ``sam``, ``system_status``, ``scheduling`` and ``webapp``,
and deliberately not a submodule of any of them — see ``README.md`` in this
directory for the full rationale.

The short version: the two clients that exist today live in *different*
packages (``sam.queries.notifications`` and
``system_status.queries.task_runs``), and those two packages import nothing
from each other. Putting the shared code inside either one would create that
edge. So it lives here, imports **only SQLAlchemy**, and creates no edges at
all.

``tests/unit/test_faceted_queries.py`` holds a subprocess import-graph gate
that enforces the "only SQLAlchemy" half of that contract.
"""

from querykit.faceted import LogSpec, count_rows, facet_counts, page_rows

__all__ = ['LogSpec', 'count_rows', 'facet_counts', 'page_rows']
