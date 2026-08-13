# A lookback control for the audit-style filter panels

> **Status: implemented.** Previously `docs/plans/FILTER_LOOKBACK_PILLS.md`,
> which was parked on a UX decision. That decision never had to be made — see
> [§ The blocker dissolved](#the-blocker-dissolved). Retitled because the answer
> is not pills.

## What this was

`audit_filters` (Transactions + Adjustments) and `xras_filters` (the XRAS action
log) presented their window as a bare `From` / `To` date pair, while every other
historical surface offered a lookback affordance. They now carry the same
`age_band_range` ladder the jobs and disk-scans explorers use, writing the same
`start_date` / `end_date` fields those panels always submitted.

## The blocker dissolved

The original plan proposed reusing `window_pills`, and blocked on this:

> **Audit's default-window rule distinguishes *absent* from *empty*, and the
> pills can only produce *empty*.**

That was correct **for that mechanism**. `window_pills` sets a `days` field and
*blanks* the date pair, and `_parse_audit_filters` reads present-and-empty
bounds as "all time" — so a `90d` pill would have widened the window to
everything. Repairing the rule would have deleted the only all-time escape
hatch, which is why the doc stopped and listed three replacements.

None was needed. PR #438 landed `age_band_range` / `ladder_range`, a control
that writes the date pair **directly**, resolved server-side. `_age_band_ctx`
in `webapp/jobs/routes.py` had already recorded why that matters:

> The control writes `start`/`end` directly and never touches `days`, which
> keeps it clear of the one asymmetry in this module — `days` outranks an
> explicit range in `_parse_job_filters`.

Pointing that control at these three panels changes **no route parser and no
window semantics**. The panels submit exactly the pair they always submitted;
only the thing the operator touches changed.

### Two things the original doc had wrong

* **"First load takes the absent branch."** It does not. All three pages
  *pre-fill* the 30-day pair, so every request from the UI has always taken the
  explicit branch. The absent branch fires only for a bare fragment GET.
* **The all-time affordance was never discoverable.** `Clear filters` runs
  `form.reset()`, which restores the *pre-filled* dates — so "empty both boxes"
  was reachable only by hand, and nothing said so. The ladder's open-ended top
  band now does it in one drag.

## What "all time" actually means here

Worth stating precisely, because the two thumbs are **crossed** and the naive
reading is wrong. `start_date` is fed by the HIGH thumb and `end_date` by the
LOW one. Dragging the high thumb to `2+ Years` therefore writes:

    start_date = ''          (the open-ended band's older edge is None)
    end_date   = <today>     (the low thumb is still on band 0)

So it is *unbounded below, bounded at today* — **not** "both bounds empty".
That is identical to what the panel already submitted on every load, so nothing
regressed; but it is not identical to manually clearing both boxes, which also
drops the ceiling. The distinction only matters on the XRAS log, whose
*absent*-branch default is deliberately unbounded above
(`_parse_xras_filters`, and `TestDefaultWindowUpperBound`). True
unbounded-above remains reachable by revealing the exact inputs and clearing
`To`. `test_the_open_ended_band_clears_only_the_older_bound` pins the shape.

## As built

| | |
|---|---|
| **Ladder** | `AUDIT_AGE_BANDS` in `dashboards/allocations/blueprint.py` — 7 / 30 / 90 / 180 / 365 / 730 / open-ended. Byte-identical to `JOBS_AGE_BANDS` and deliberately *not* imported from it: ladders are domain-owned (cf. `ATIME_BUCKETS`), and importing `webapp.jobs.service` would couple three ungated allocations pages to a plugin-adjacent module. |
| **Context** | `_window_control_context(anchor, start, end)` — the sibling of `_age_band_ctx`. Feeds `age_bands`, `age_band_span`, `layout`. Normalizes both bounds with `or None`, because `bands_for` tests `is None`, not falsiness — a raw `''` would render "custom" for what is actually the open-ended band. |
| **Templates** | `audit_filters.html`, `xras_filters.html` — the two date `<div>`s become one `age_band_range` call. Row alignment moved `-start` → `-end`, matching the two panels that already carry this control. |
| **No fallback branch** | Unlike `_jobs_filters` / `_disk_scans_dir_filters`, which degrade because their ladder comes from an optional plugin. Here the anchor is `now` and the ladder is a constant, so a fallback would be untestable dead code. ⚠️ `ladder_range` renders **nothing at all** when `bands` is empty, and Jinja will not raise for a forgotten argument — `test_audit_panels_render_exactly_one_named_date_pair` is the guard. |
| **Parsers** | Untouched. `dashboard.css` and `test_css_tokens.py` untouched — `.ladder-range` already solved the on-navy contrast the old doc cited (1.76:1 vs 11.7:1). |

### ⚠️ The four-way coupling

Band 1's upper bound is `30`. So is the `timedelta(days=30)` in
`_parse_audit_filters`, `_parse_xras_filters`, `_audit_page_context` and
`xras()`. That is what makes the resting control name its window instead of
rendering "Custom range" on every first load. Change one and you must change
all four — and nothing else fails, because the *filter* stays correct.
`test_the_default_audit_window_is_a_whole_span` is the tripwire.

## Two shared-control bugs fixed on the way

Both were pre-existing on the disk-scans explorer, and this change would have
shipped them onto three more pages.

* **`Clear filters` left the ladder painted.** `form.reset()` restores input
  values — including the thumbs — but cannot undo what JavaScript drew: the
  fill bar's inline `--lo`/`--hi`, the readout, `aria-valuetext`, and the
  `--custom` modifier all survived it. Clear therefore snapped the thumbs back
  while the bar and readout still described the *previous* window.
  `form-reset-submit` now repaints every ladder in the form.
* **Typing an exact date left the readout claiming a span it no longer held.**
  The control actively misdescribed the filter it was about to submit. A typed
  value now marks the control custom (`ladder-range-typed`). It deliberately
  does not try to detect that a typed value lands on a band edge — `bands`
  carries per-band bounds, not cumulative spans, so that needs the arithmetic
  this control keeps server-side. Erring toward "custom" understates, which is
  the safe direction.

## Deliberately not done

* **`_audit_page_context()` still ignores `request.args`.** These pages have
  never honored a deep-linked `?start_date=`; the fragment reads the panel, and
  the panel renders defaults. Making them honor it is a feature, not part of
  this change — but note the consequence: after typing a custom range and
  submitting, a *reload* returns to the 30-day default.
* **`window_pills` is untouched**, and keeps its single caller (the XRAS
  activity card, on a card surface where `btn-outline-secondary` is correct).
  The navy `variant` the old doc wanted is moot.
* **Mobile-first-visit renders desktop.** `layout` comes from a cookie
  `layout-axis.js` writes *after* the server has answered, so the very first
  page view at a new size gets the wrong presentation until a reload.
  Pre-existing on disk-scans; now on three more pages.

## Verification as run

* **`pytest`: 6,290 passed, 42 skipped, 1 xfailed** (from 6,248), +42 tests in
  `tests/unit/test_audit_window_filters.py` and `test_audit_window_control.py`.
  `test_xras_dashboard.py`'s three parser tests pass **unedited** — the proof
  the parsers are untouched.
* **`make e2e`: 78 passed, 2 skipped**, zero console errors. The dark-mode tier
  matters most here: `/allocations/transactions` is in both `PAGES` and
  `SWEEP_PAGES`, and **no page in either list previously carried a
  `.ladder-range`** — jobs and disk-scans are plugin-gated and CI runs without
  them. So this put the ladder's chrome under `MIN_CONTRAST = 3.0` for the
  first time. Measured on the navy panel: **11.73:1**.
* **Browser, all three panels.** Transactions totals move
  420 (30d) → 1,216 (90d) → **53,074** (all time), with `start_date` an empty
  string rather than the literal `"null"`. Reveal focuses the bound its own
  axis end names; typing flips the readout to "Custom range" without
  submitting; `Clear filters` restores thumbs, fill, readout and dates
  together. On `/allocations/xras`, both forms resolve `start_date` to a single
  `INPUT` — no `RadioNodeList` — and the activity card's `90D` pill sets
  `days=90` on its own form while leaving the panel's ladder at 30 days.
  Mobile renders two selects, no thumbs, exact inputs visible, no horizontal
  scroll.

  The XRAS *table* could not be exercised: the dev snapshot holds 0
  `xras_action_log` rows, so its row count cannot move whatever the window.
  Field writes, readout, and form isolation were verified there directly.
