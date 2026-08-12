# Uniform lookback pills for the audit-style filter panels

> **Status: not started.** Split out of the age-band work on `ux_polish` (see
> `AGE_BAND_FILTERS` history in that branch) because it turns on one UX decision
> that is not mine to make — see [§ The blocker](#the-blocker).

## What this is

`audit_filters` (Transactions + Adjustments) and `xras_filters` (the XRAS action
log) still present their window as a bare `From` / `To` date pair. Every other
historical surface offers convenient lookbacks:

| Surface | Control | Mechanism |
|---|---|---|
| XRAS activity card | `window_pills` macro | `?days=` into the panel form, `set-filter-submit` |
| Jobs cards | hand-rolled pill group | `?days=` via `hx-get`, URL-driven |
| Resource details (disk + HPC) | `drp_pills` + `drp_custom` | full-page nav, `pickers.js` |
| Disk-scans + jobs explorers | `age_band_range` | writes the date pair directly |
| **audit_filters, xras_filters** | **none** | — |

The intent is 7d / 30d / 90d / 180d / 1y, subset per context, using the existing
`dashboards/fragments/window_pills.html` — which is already generic over
`form_id` and its pill list and today has exactly **one** caller.

## What it needs

1. **A navy variant of `window_pills`.** It hardcodes `btn-outline-secondary`,
   and the `.btn-group` treatment in `dashboard.css` fills the inactive state
   with `--ncar-blue`. Measured against the `--ncar-navy` filter panel that is
   **1.76:1** — under the 3:1 WCAG floor for non-text UI. The panel idiom is
   `btn-outline-white` (11.7:1 for the active fill), so the macro needs a
   `variant` parameter rather than a second copy.
2. **A hidden `days` field** in each panel's form — the control
   `set-filter-submit` targets by name.
3. **Route-side `days`.** `_parse_activity_window`
   (`dashboards/allocations/blueprint.py:1387-1417`) is the reference
   implementation: explicit range outranks `days`, and it returns the raw
   strings alongside the parsed bounds so one dict re-renders the controls.

## The blocker

**Audit's default-window rule distinguishes *absent* from *empty*, and the pills
can only produce *empty*.**

`_parse_audit_filters` (`blueprint.py:756-806`):

```python
if 'start_date' not in request_args and 'end_date' not in request_args:
    # First-load default: last 30 days, ending now.
```

with the docstring stating the other half outright — *"empty bounds explicitly =
all time"*. So today, **clearing the date fields is the "all time" affordance.**

A day pill sets `days` and clears the range, because an explicit range outranks
`days` — that is what `data-clear-fields="start_date,end_date"` is for
(`window_pills.html:44-47`). But cleared fields submit as **present and empty**,
which audit reads as *all time*. The pill would therefore widen the window to
everything instead of narrowing it to N days: the same
"pill appears to do nothing" failure the macro documents, arriving through a
different rule than the one it was written against.

Fixing the rule — empty falls back to `days`/default rather than meaning all
time — is straightforward, but it **removes the all-time escape hatch**. So the
question is what replaces it:

| Option | Cost |
|---|---|
| An **`All`** pill (sentinel `days=0`) | One extra pill; `_parse_*` must treat 0 as unbounded. Most discoverable, and makes an affordance out of something currently undocumented. |
| Keep absent-vs-empty, and have the pills **omit** rather than clear the fields | No semantics change, but needs a new JS action — `set-filter-submit` can only blank a field, not remove it from the submission. `disabled` fields are omitted by htmx, so it may be a one-line variant. |
| Drop "all time" entirely | Cheapest; loses a capability someone may rely on for an audit surface. |

XRAS is easier either way: its default is a 30-day lower bound with a
**deliberately unbounded upper** (`blueprint.py:1189-1252`), so it has no
all-time question — only audit does.

## Verification when it lands

- `pytest` from **6,197**. The audit/XRAS default-window tests are the ones to
  watch: `tests/unit/test_xras_dashboard.py:379-396` (default window unbounded
  above) and `:406-416` (an explicit end date still bounds, asserting the exact
  `23:59:59`).
- `pytest tests/unit/test_css_tokens.py` — the navy variant must use role
  tokens; `--surface-on-brand` / `--surface-on-brand-rgb` already exist for
  exactly this (added by the age-band work).
- Browser: click each pill and confirm the window *narrows*; then confirm
  whatever all-time affordance is chosen actually reaches all time. That is the
  regression the blocker describes, and it is invisible to the unit tier.
