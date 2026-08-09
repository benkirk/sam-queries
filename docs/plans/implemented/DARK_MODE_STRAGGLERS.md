# Dark mode: the stragglers

**Branch**: `dark_mode_stragglers` (off `staging` @ 186c311). PR base `staging`.
Follow-up to PR #419 (app-wide dark mode) and its § *PR 4 as built*.

## Context

PR #419 retired SAM's own hardcoded surfaces onto tier-2 role tokens and the
app reads well in dark mode. What it did not reach is a specific class of
surface: **the ones Bootstrap owns and does not retheme**, plus a handful of
inline literals in templates that no stylesheet can reach. Four were reported
from real use within a day of the merge:

| Reported | Measured, in the browser, dark theme |
|---|---|
| resource-details month table has an invisible chevron | `.table-primary` row paints `#cfe2ff`; the chevron is `.text-muted` = `rgba(222,226,230,.75)` → **1.01:1** |
| project tree's outermost node is too bright | `<li style="background: #fff3cd">` under `--text-primary` (#e9ecef) → light-on-light |
| the 3 tabs on `/admin/project/<code>/edit` are bright | `.tab-content.bg-white` = `#fff` with `color: rgb(233,236,239)` → **white on white** |
| `/admin/resources` innermost rows are bright | `.table-secondary` cells paint `#e2e3e5`; `.text-muted` cells on them → ~1.1:1. Nested `table.bg-white` → white on white |
| `/allocations/projects` "Total" row shows up black | `<tfoot class="table-secondary">` forces `--bs-table-color: #000` over a `--surface-secondary` (#2a3745) cell → **black on slate** |

They are five symptoms of **three** mechanisms, all of them systemic:

1. **Bootstrap contextual `.table-*` classes.** `.table-primary` /
   `.table-secondary` / `.table-info` / `.table-warning` hardcode
   `--bs-table-bg` to a light tint *and* `--bs-table-color: #000`, and
   Bootstrap's `[data-bs-theme=dark]` block redefines none of them. This is the
   same defect `.table-subtle` was created for (dashboard.css:958) — that fix
   covered `.table-light` only, and the other five variants were left. **24
   occurrences in 11 templates.** Note both halves bite independently: where a
   SAM rule already supplies a dark background, the `#000` *colour* half
   survives on its own, which is the `/allocations/projects` Total row.
2. **Theme-blind utilities**: `bg-white` (**4 sites**). `.bg-light` and
   `.bg-dark` are already absent; `bg-body-*` is themed and fine.
3. **Inline `style=` literals in templates** — unreachable by any stylesheet.
   **20 literals across 8 templates**, of which the tree highlight (`#fff3cd`,
   5 sites) and the rolling-rate bar (6 literals) are the ones that actually
   render wrong.

Plus one straggler in CSS: `.date-group-header td { background-color: #f1f3f5 }`
(dashboard.css:1017) — a light row background with no dark counterpart.

**Out of scope, deliberately.** The 35 `text-dark` / 13 `text-white` hits the
inventory turned up are *not* defects: they sit on saturated brand fills
(`badge bg-warning text-dark`, `modal-header bg-primary text-white`), which is
the `--text-on-brand` invariant PR 3 established. Spot-measured on
`/user/resource-details`: the gold badge renders `rgb(255,218,106)` on
`rgb(51,39,1)` in dark — already correct. Also out: Flask-Admin (documented out
of scope in DARK_MODE.md), and the `rgba(0,0,0,…)` box-shadows, which the
ratchet's own comment already judged theme-neutral.

## The work

### 1. Contextual table variants — one dark block, no template churn

In `dashboard.css`, immediately after `.table-subtle`, add a dark-only override
for the five remaining variants, mapping each onto Bootstrap's `-bg-subtle`
palette (which *does* retheme — the same lever `status.css:17-44` already uses
for the status badges), with the text colour on `--text-primary` exactly as
`.table-subtle` chose:

```css
:root[data-bs-theme="dark"] .table-primary   { --bs-table-bg: var(--bs-primary-bg-subtle);   … }
:root[data-bs-theme="dark"] .table-secondary { --bs-table-bg: var(--bs-secondary-bg-subtle); … }
/* success / info / warning / danger likewise */
```

Each block mirrors `.table-subtle`'s full variable set — `--bs-table-color`,
`--bs-table-border-color`, and the `striped` / `active` / `hover` pairs — because
those all carry `#000` in the stock class and each will surface somewhere
eventually.

**Why override rather than migrate 24 templates to a SAM class**, which is what
`.table-subtle` did: the tint is load-bearing here in a way it was not for
`.table-light` on a `<thead>` — `project_directories_card.html` uses
`table-warning` to mark an orphaned directory row *inside* a `table-secondary`
table. Preserving the semantic name at the call site keeps that readable, and a
dark-only rule leaves light mode byte-identical by construction. Deviation from
the `.table-subtle` precedent, so it gets a comment saying this.

### 2. `bg-white` → `bg-body` (4 sites)

`--bs-body-bg` is bridged to `--surface-card`, which is `#ffffff` in light — so
this is byte-identical in light mode and follows the card surface in dark.
Sites: `admin/edit_project.html:80`, `admin/fragments/facility_card.html:95`,
`admin/fragments/resources_card.html:135`,
`user/partials/_resource_details_macros.html:97`.

### 3. Tree "current node" highlight (5 inline sites)

Replace the inline `background:#fff3cd; border-left-color:#ffc107;` with one
class defined in `dashboard.css`:

```css
.tree-node-current    { background: var(--bs-warning-bg-subtle);
                        border-left-color: var(--bs-warning); font-weight: 700; }
tr.tree-node-current  { --bs-table-bg: var(--bs-warning-bg-subtle);
                        --bs-table-color: var(--text-primary); }
```

`--bs-warning-bg-subtle` is `#fff3cd` in light (byte-identical) and `#332701` in
dark. The `<tr>` variant sets `--bs-table-bg` rather than `background`, because
Bootstrap paints each `<td>` opaquely over any row-level background — the trap
already documented at `components.css:14`.

Sites: `user/resource_details.html:376`, `user/resource_details_disk.html:19`
and `:217`, `shared/project_tree.html:62` and `:198`.

### 4. Rolling-rate bar + the small inline literals

`user/fragments/rolling_rate_htmx.html`: track `#e9ecef` →
`var(--surface-secondary)` (**exactly** `#e9ecef` in light); steady-state marker
`#343a40` → `var(--text-primary)`; threshold label chip
`rgba(255,255,255,0.82)` → `rgba(var(--surface-card-rgb), 0.82)`; axis labels
`#6c757d` → `var(--text-secondary)`. The `#dc3545` threshold line stays — danger
is meaning-bearing and invariant by the tier-1 rule.

Then the remainder of the inline set: `#ccc` status dots (institutions_table,
organization_card) → `var(--text-tertiary)`; `#dee2e6` / `#ced4da` row rules in
`project_allocation_tree_htmx.html` → `var(--border-default)`; the
`rgba(33,37,41,0.55)` "now" marker on the two progress bars →
`rgba(var(--text-primary-rgb), 0.55)`.

### 5. `.date-group-header` (dashboard.css:1017)

`#f1f3f5` → `var(--surface-tertiary)` (`#f8f9fa` light — a declared 4-unit
shift, the same kind `auth.css:105` documents).

**This one moves a test gate**: `tests/unit/test_css_tokens.py`'s `ALLOWED`
ratchet is an *equality* check, not a ceiling — removing a literal from
`dashboard.css` without lowering `'dashboard.css': 19` to `18` fails the suite
with "Fewer raw colours than the ratchet allows — good. Lower these in ALLOWED."

## Regression coverage

`e2e/test_dark_mode.py` samples 8 hand-picked selectors on 3 pages. Every defect
above sat outside that sample — the lesson of Appendix E, again, at one more
remove. Add a dark-only **leaf-text contrast sweep**: for each visible element
that carries its own text, composite the ancestor backgrounds (the existing
`_EFFECTIVE_COLOURS_JS` walk does exactly this) and fail below `MIN_CONTRAST`.

- Dark only. Light mode is the identity here and a sweep over it just doubles
  runtime.
- Page list seeded with the surfaces above: `/admin/resources`,
  `/allocations/projects`, plus the existing `PAGES`. Static routes only — the
  `<projcode>` edit page can't be reached without assuming a projcode survives
  the obfuscated snapshot.
- Collapsed content has zero size and is skipped, so the sweep clicks the
  page's `[data-bs-toggle="collapse"]` triggers first (bounded, and only those
  whose target is a `.collapse` in the document) — the nested `/admin/resources`
  tables are exactly the case that only exists after expansion.
- Any legitimate exception gets a named allowlist entry with a reason, on the
  `ALLOWED_CONSOLE` model — which is deliberately empty, and should stay that
  way here too.

## Verification

1. `docker compose up webdev --watch`, then walk the five reported surfaces in
   both themes at <http://127.0.0.1:5050>: `/user/resource-details/NCIS0001?resource=Casper`
   (month table + tree), `/admin/project/NCIS0001/edit` (3 tabs),
   `/admin/resources` (expand a Resource, a Contract, a Facility),
   `/allocations/projects` (Total row).
2. Measure, don't eyeball — re-run the same computed-contrast probe used to
   produce the table at the top of this doc; every sampled label ≥ 3:1.
3. `make e2e` (needs `make docker-up`) — the new sweep must come up clean with
   an empty allowlist.
4. `pytest tests/unit/test_css_tokens.py tests/unit/test_template_csp_lint.py`
   — the ratchet at 18, CSP lint untouched (it permits `style=` attributes; only
   `<style>` blocks, `on*=` and `hx-on:` are violations).
5. Full suite. No chart fingerprint delta is expected: nothing here touches
   `--surface-card` or `charts/theme.py`, and a delta would mean one of them
   moved.

## As built

Everything above shipped as planned. What the plan did not predict is what the
sweep found once it existed — it went red on **nine** surfaces the five reports
had not mentioned, on the first run:

| Found | Was | Now |
|---|---|---|
| `/status/derecho` outage banner, 4 elements | `.alert-warning` set `color: var(--text-heading)` — space-blue in light, **near-white on `--ncar-orange` (1.77:1) in dark** | a new invariant token, `--text-on-brand-dark` |
| the same banner's `.text-muted` line | `rgba(222,226,230,.75)` on orange = **1.39:1** | `.alert .text-muted { color: inherit !important }` — Bootstrap's colour utilities carry `!important`, so the first pass at this silently did nothing |
| `/user/info`, `/user/accounts` badges | `.text-secondary` — Bootstrap does NOT retheme `--bs-secondary-rgb`, unlike `.text-body-secondary` | swapped at 9 sites |
| progress-bar `%` labels, 4 | white on `--success-color` **2.54:1**, on `--warning-color` **2.15:1** | `--text-on-brand-dark`. Theme-invariant — equally unreadable in light, so this one is a **declared legibility change in both themes**, not a dark repair |

`--text-on-brand-dark` is the plan's one architectural addition: `--text-on-brand`
is white, for saturated brand fills, and there was no name for the opposite
case — dark ink on a brand fill that is itself light (gold, orange). It is
invariant for the same reason its sibling is, valued at `--ncar-space-blue`,
which is what `--text-heading` already resolved to in light, so every surface
adopting it is byte-identical there. `INVARIANT_TOKENS` in
`tests/unit/test_css_tokens.py` now names the set once, because two tests
needed the same list and a token in one but not the other is exactly the
half-wired state they exist to catch.

The `.tree-node-current` rule also needed a second selector: `.tree-list li`
(0,1,1) outranks a bare class, so the first draft rendered the current node
identical to every other node — caught by measuring, not by looking.

## Light mode must not move

Every substitution above is either byte-identical in light (`--surface-card`
`#ffffff`, `--surface-secondary` `#e9ecef`, `--bs-warning-bg-subtle` `#fff3cd`)
or a declared small shift, listed here: `#f1f3f5`→`#f8f9fa`, `#ccc`→`#97999b`,
`#dee2e6`/`#ced4da`→`#E2E8F0`, `#343a40`→`#323133`, `#6c757d`→`#718096`,
`.text-secondary`→`.text-body-secondary` (#6c757d → rgba(50,49,51,.75) over
white). Verified after the fact by reading the computed values back in the
browser: the month row is still `rgb(207,226,255)`, the `table-info` row
`rgb(207,244,252)`, the tree node `rgb(255,243,205)` with a `rgb(255,193,7)`
left border at weight 700 — all byte-identical to before.

The **one** intended visible change in light mode is the progress-bar `%`
label, white → space-blue on the green and amber fills. It is in the As-built
table above and nowhere else.
