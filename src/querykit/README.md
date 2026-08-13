# `querykit` — the faceted-log query facade

Model-agnostic read helpers for the "filtered, paginated log table with facet
chips above it" shape. Imports **only SQLAlchemy**.

```python
from querykit import LogSpec, count_rows, facet_counts, page_rows
```

---

## Why this is a top-level package

Three tables render this shape: `xras_action_log`, `notification_log`, and
`task_run`. The first two live in `sam/queries/`; the third lives in
`system_status/queries/`.

**`src/sam/` and `src/system_status/` import nothing from each other**, in
either direction. `SCHEDULED_TASKS.md` § 6.1 protects the `sam → system_status`
direction specifically. Putting the shared code in `sam/queries/faceted.py`
would have created the *reverse* edge — `system_status` importing `sam` — which
is new and worse.

**Why not `webapp/`, since the webapp is the HTML union of both?** Because the
query half is not webapp-only. `src/cli/xras/builders.py` already imports
`summarize_xras_actions`, the function this facade is meant to absorb when XRAS
is retrofitted. After that retrofit, `sam-admin xras --summary` would import
`webapp`. That is free today — `webapp/__init__.py` is docstring-only — but it
is a landmine: `tests/unit/test_notify_import_graph.py` exists because this
exact class of coupling already produced a real `ImportError` in this repo
(`sam.fmt` → the top-level `config`, shadowed by `webapp/config.py` when
`src/webapp` lands at `sys.path[0]`).

A peer package imports nothing and is imported by everyone. Zero new edges.
`src/scheduling/` is the recent precedent for adding one.

---

## What belongs here

Dialect-neutral, model-agnostic **read** helpers over a declarative spec:
count, page, facet.

## What does not

| Not here | Where instead | Why |
|---|---|---|
| Per-table `_filters()` bodies | the table's own query module | Genuinely bespoke SQL — `ilike` across different columns, index-friendly `IN` forms. A DSL would cost more than it saves. |
| Anything importing an ORM model | the owning package | Inverts the layering this package exists to avoid. |
| Anything Flask-aware | `webapp/utils/faceted_log.py` | That is the deliberate other half: `parse_window`, `build_facet_strip`. |
| Write paths | `sam/manage/`, model `create`/`update` | Read-side only. |

## The admission rule

**A helper moves in on its third real caller, not its second.**

That is the same reasoning that justifies the package at all — it was extracted
because notifications and tasks would have been the *third* copy of the pattern
— and the same discipline `CLAUDE.md` states for `CrudSpec`: *an entity needing
more than the spec expresses stays bespoke; don't grow the spec for one case.*

Two clients is a coincidence. Three is a pattern.

---

## Known growth, in likely order

1. **The XRAS retrofit** (deferred out of PR #444). It is the real test of this
   design: XRAS brings a sort whitelist (`XRAS_ACTION_SORT_COLUMNS`), alias
   canonicalization (`canonical_action_type` / `expand_action_types`), a
   correlated `recheck_count` subquery and `_annotate_project_existence`. Some
   of that is facade material and some stays bespoke; deciding which is that
   retrofit's job.
2. **The jobs-explorer facets** (`_jobs_facet_chips.html`) are a third partial
   implementation of the chip pattern — a candidate once XRAS proves the shape.

---

## The contract is gated, not merely documented

`tests/unit/test_faceted_queries.py` runs a subprocess import-graph check:
importing `querykit` must not pull in `flask`, `sam`, or `system_status`.
Without it, "imports only SQLAlchemy" is a comment — and comments drift.
