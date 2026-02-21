# Webapp Styling — Future Work

Deferred items from the CSS/JS structural audit completed in commit `98eefba`
(branch `styling`).  Items are grouped by priority.

---

## HIGH — Bugs / HTML Validity

### 1. Pre-existing duplicate `class=` attributes
Multiple templates have two `class=` attributes on the same element.  Browsers
silently use the first one; the second is ignored.  This is **invalid HTML** and
will cause layout surprises if the wrong attribute is the active one.

| Template | Occurrences | Example |
|---|---|---|
| `templates/admin/index.html` | 3× | `<div class="row" class="section-spacing">` |
| `templates/dashboards/admin/dashboard.html` | 1× | `<div class="card-header" class="cursor-pointer" …>` |
| `templates/dashboards/user/resource_details.html` | 6× | card-headers, progress bars, chevron icons |
| `templates/dashboards/user/partials/collapsible_card.html` | 2× | card-header, chevron icon |
| `templates/dashboards/user/partials/project_card.html` | 2× | card-header, progress bar |

**Fix**: Merge both `class=` values into a single attribute on each element.

---

### 2. Event listener re-attachment in `admin-expirations.js`
`attachImpersonateHandlers()` and `attachProjectCodeHandlers()` are called on
every `loadExpirations('abandoned')` request without first removing the previous
listeners.  A second load accumulates a second set of handlers on the same
elements, causing double-fires (double confirm dialogs, double fetches).

**Fix**: Use delegated event listeners (`.on('click', selector, fn)` attached
once to a stable parent) instead of direct listeners reattached after each
inject, or call `.off('click')` before `.on('click')`.

---

## MEDIUM — Technical Debt

### 3. `API_BASE` constant duplicated
`const API_BASE = '/api/v1'` is defined separately in both
`allocation-management.js` and `member-management.js`.

**Fix**: Move to `utils.js` as `window.SAMUtils.API_BASE` and reference from
both files.

---

### 4. Inline script in `jobs_table.html`
`templates/dashboards/user/fragments/jobs_table.html` has a ~27-line `<script>`
block defining `loadJobsPage(pageNum)` inline, called via `onclick=` handlers.

**Fix**: Extract to `static/js/jobs-table.js` (same pattern as the allocations
dashboard extraction in phase 3).

---

### 5. Four `!important` declarations in `dashboard.css`
Lines 50, 58, 63, 142 use `!important` on `.navbar-brand`, `.navbar-nav
.nav-link`, and `.card-header`.  This indicates selector specificity problems
and makes future overrides harder.

**Fix**: Audit what Bootstrap rule is winning and write a more specific selector
instead of fighting it with `!important`.

---

### 6. `auth.css` has its own `:root` color block
The login page only loads `auth.css` (not `dashboard.css`), so it cannot
inherit the shared CSS variables.  This forces `auth.css` to re-declare five
NCAR brand colors as well as several additional hardcoded values (`#003579`,
`rgba(0,153,204,…)`, `#4A5568`, `#718096`, `#E2E8F0`).

**Fix**: Extract a minimal `static/css/variables.css` containing only `:root {
… }` and load it from _both_ `base.html` and `login.html`.  All other CSS files
can then use variables freely without duplication.

---

### 7. Remaining hardcoded colors in `auth.css`
Even after a shared variables file is in place (item 6 above), replace:

| Hardcoded value | Should become |
|---|---|
| `#003579` | new `--ncar-navy-mid` token (between navy and blue) |
| `rgba(0, 153, 204, …)` | `rgba(var(--ncar-teal-rgb), …)` |
| `#4A5568` | `var(--ncar-gray)` |
| `#718096` | `var(--ncar-gray)` or new `--color-gray-muted` |
| `#E2E8F0` | `var(--border-color)` |
| `#ffc107`, `#ffeeba`, `#856404` | Bootstrap/semantic tokens |

---

### 8. Inline `border-left` styles with `!important` in `jupyterhub.html`
Lines 83–116 have four patterns like:
```html
<div class="card border-start" style="border-left: 4px solid #198754 !important;">
```

**Fix**: Add semantic CSS classes to `status.css` (e.g. `.border-status-success`,
`.border-status-info`) that encode the severity colour, eliminating both the
inline style and the `!important`.

---

## LOW — Quality of Life

### 9. `member-management.js` loaded unconditionally
`base.html` loads `member-management.js` (308 lines) on every dashboard page,
even pages that have no member-management UI.

**Fix**: Move the `<script>` tag into the `{% block extra_js %}` of the user
dashboard and admin dashboard templates where it is actually needed.

---

### 10. jQuery vs. vanilla JS mixing
All JS files mix `document.getElementById()` with `$('#…')`, `addEventListener`
with `.on()`, and `fetch()` with `$.ajax()` — sometimes within the same
function.  There is no consistent rule.

**Fix**: Adopt a project-wide convention (recommend: vanilla JS for DOM access
and `fetch()`; keep jQuery only for Bootstrap 5 event helpers where needed) and
document it in a brief `docs/JS_CONVENTIONS.md`.

---

### 11. Remaining hardcoded border-radius values
`allocations.css` still uses `0.25rem` in a few selectors (e.g.
`.overview-section`) that were not updated to `var(--radius-sm)` during the
phase-2 token sweep.  A global search-replace across the remaining CSS files
would finish the job.

---

*Last updated: 2026-02-21 — audit commit `98eefba` on branch `styling`*
