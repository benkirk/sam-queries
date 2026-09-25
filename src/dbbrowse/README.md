# `dbbrowse` — read-only table browsing over any engine

The query half of the `/database` browser (`src/webapp/db_browser/`). Imports
**only SQLAlchemy**, so a CLI can use it without Flask, and it works the same on
SAM, `system_status`, and the plugin databases (job_history, fs_scans).

```python
from dbbrowse import read_only_connection, load_catalog, reflect_table, fetch_page
```

## Why not `querykit`

`querykit` pages an ORM model through a declarative `LogSpec`. This package
pages *reflected* `Table` objects over a raw `Connection` whose safety comes
from the dialect guard, not the model. They share no code.

## The rules

| Rule | Where |
|---|---|
| Every query runs inside `read_only_connection`: a READ ONLY transaction, a statement timeout, always rolled back. Unknown dialects raise. | `readonly.py` |
| Identifiers come from reflection (`Table.c`), never from a string. Values are always bound. | `filters.py`, `query.py` |
| No `COUNT(*)` on the page path: counts are catalog estimates; `exact_count` runs only when asked. | `catalog.py`, `query.py` |
| A redacted column is never selected, filtered, or sorted. | `redact.py` |
| Nothing imports `flask`, `sam`, `system_status` or `webapp`. ORM knowledge arrives as a registry argument. | `overlay.py`; gate: `tests/unit/gates/test_dbbrowse_import_graph.py` |

## What does not belong

Anything aware of requests, permissions, or URLs — that is
`webapp/db_browser/`. Write paths of any kind.
