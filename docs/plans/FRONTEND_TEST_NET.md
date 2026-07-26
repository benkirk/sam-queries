# Front-end Regression Net (and why not a JS unit harness)

**Status: PLANNED — not started.**
Branch: `frontend_test_net` (docs-only PR first; implementation lands on the
same branch, updating the PR as it goes). Targets `staging`.

> Deliverable of *this* PR: this document only. `docs/**` is in the
> `paths-ignore` list for both `sam-ci-docker.yaml` and `test-install.yaml`, and
> `mega-linter.yaml` only triggers on PRs to `main`/`master` — so a docs-only PR
> to `staging` runs essentially no CI. No `[skip ci]` needed.

## Progress

- [ ] Step 0 — this doc committed, PR opened against `staging`
- **Part 1 — Python shell-contract guards (no new dependencies)**
  - [ ] 1.1 `tests/unit/test_modal_shell_contract.py` (generalizes the
        `test_modal_shell_pairing.py` added in PR #378)
  - [ ] 1.2 script-order assertion on `dashboards/base.html`
  - [ ] 1.3 prove it bites — remove the `allocation_modals.html` include, confirm
        a named failure, restore
- **Part 2 — Playwright console sweep**
  - [ ] 2.1 `e2e/` skeleton: `pytest.ini`, `conftest.py` (base_url, error
        capture, admin login)
  - [ ] 2.2 `test_console_sweep.py` — ~16 routes + ~5 declared flows
  - [ ] 2.3 `test_pr378_regressions.py` — the two bugs from PR #378
  - [ ] 2.4 `pyproject.toml` — new `e2e` extra
  - [ ] 2.5 prove the net works — re-introduce the dead pencil, confirm the
        sweep names `#editAllocationFormContainer`, restore
- **Part 3 — CI**
  - [ ] 3.1 two new steps in `test-install.yaml`
  - [ ] 3.2 confirm bare `pytest` still collects ~3,100 and never touches `e2e/`
  - [ ] 3.3 measure added wall-clock; decide on `actions/cache` (deferred by default)
- [ ] `docs/TESTING.md` — browser-tier section

---

## Context

PR #378 fixed two front-end bugs that no automated test could have caught:

1. **A dead edit pencil** inside the project-details modal on `/status/*` and
   `/allocations/{transactions,adjustments}` — the modal shell it targeted was
   never included on those pages. Bootstrap found no `#editAllocationModal`,
   htmx aborted on a dangling `hx-target`, and *nothing* surfaced: no modal, no
   network request, no visible error.
2. **A scroll overshoot** — search-result clicks scrolled past the loaded card's
   title, because the scroll was computed before the results list above it was
   cleared.

Both are pure front-end. The repo has ~3,100 Python tests and zero coverage of
any of the 2,571 lines of JavaScript.

### Why not a JS unit harness (vitest/jsdom) — rejected

- ~87% of the JS is htmx-swap/DOM lifecycle glue. The genuinely pure,
  unit-testable logic is ~150 LOC, and **none of it is exported** — every file
  in `static/js/` is an ES5 IIFE with no module system, so `compareKeys`,
  `dedupeSlashes`, `toLocalNaiveISO` et al. are unreachable from outside today.
- **jsdom zeroes out `getBoundingClientRect()`**, so bug #2 above is untestable
  there by construction — the exact class of bug that prompted the question.
- It would be the repo's first node dependency, in a codebase that hand-vendors
  Bootstrap/htmx/jQuery with SRI pins **specifically** to keep npm out of the
  serving path (`src/webapp/static/vendor/README.md`).

Cost high, catch rate low, philosophy backwards. Revisit only if the JS ever
grows real exported domain logic. The one honourable mention: `path-preview.js`
deliberately mirrors the server's `_assemble_directory_name()`, so a divergence
there is a real silent bug class — but one worth covering with a single
end-to-end assertion, not a toolchain.

### What we build instead

Two cheap, complementary layers, both driven by pytest:

| Layer | Catches | Cost |
|---|---|---|
| Python shell-contract guards | dead `data-bs-target` shells (**silent in the browser**) | no new deps, ~0s in the existing suite |
| Playwright console sweep | dead `hx-target`, JS exceptions, script-order breaks | one dev dep, ~60–90s on a non-critical workflow |

The complementarity is the point, and was **verified empirically against the
live app** while writing this plan: a dangling `hx-target` *does* emit
`console.error("htmx:targetError")` in htmx 2.0.4, so the browser sweep catches
it — but a dangling `data-bs-target` produces **no console output at all**
(htmx's `logger` defaults to `null`, and `htmx-config.js` listens for
`responseError`/`sendError`, not `targetError`). PR #378's pencil had both; a
Bootstrap-only affordance would slip past a browser-only net.

---

## Part 1 — Python shell-contract guards (no new dependencies)

### 1.1 Generalize `tests/unit/test_modal_shell_pairing.py`

Rename/extend to `tests/unit/test_modal_shell_contract.py`. It renders pages
*and the htmx fragments they can load* through the existing Flask test client,
then asserts every modal-shell id referenced by a fragment resolves in the host
page's DOM.

```python
# shell id -> the fragment template that defines it (documentation + failure msg)
SHELL_DEFS = {
    'editAllocationModal':     'dashboards/user/fragments/allocation_modals.html',
    'projectDetailsModal':     'dashboards/shared/project_details_modal.html',
    'userDetailsModal':        'dashboards/user/fragments/user_details_modal.html',
    'groupMembersModal':       ...,
    'addExemptionModal':       ...,
    'editExemptionModal':      ...,
    'allocateDownModal':       ...,
    'exchangeAllocationModal': ...,
}

# page URL -> htmx fragment URLs reachable from it (the declared bit; small,
# and exactly the knowledge that matters)
PAGE_FRAGMENTS = {
    '/status/derecho':           ['/user/project-details-modal/{projcode}'],
    '/allocations/transactions': ['/user/project-details-modal/{projcode}'],
    '/admin/projects':           ['/admin/project/{projcode}', ...],
    ...
}
```

For each pair: GET the page, collect `id="..."`; GET each fragment, collect every
`hx-target="#x"` / `data-bs-target="#x"`; assert `x` ∈ (page ids ∪ fragment ids),
with a failure message naming the missing shell **and** the template that defines
it (from `SHELL_DEFS`).

Uses `auth_client` and the `active_project` fixture. Keep the existing
`test_edit_project_page_ships_one_of_each` case.

**Precedent to follow:** `tests/unit/test_template_csp_lint.py` — same shape
(explicit registry, exact-equality ratchet, docstring explaining what to do
instead when it fires).

### 1.2 Script-order guard (~10 lines)

`actions.js` defines `window.registerAction` / `window.revealCard`, and five
later scripts call them at eval time. The order in
`src/webapp/templates/dashboards/base.html:218-235` is load-bearing and
undeclared — reordering silently breaks every `data-action` on every page.
Assert `actions.js` appears before `pickers.js`, `dashboard-init.js`,
`admin-cards.js`, `modals.js`, and `form-helpers.js`.

---

## Part 2 — Playwright console sweep

### Placement: `e2e/` at the repo root, **outside** `tests/`

A deliberate design decision, not tidiness. `tests/conftest.py`'s
`pytest_configure` safety guard hard-exits (code 2) unless `SAM_TEST_DB_URL`
points at an allowlisted `(host, port)`. Browser tests talk HTTP, never the DB,
and `test-install.yaml` doesn't start `mysql-test` at all — its `mysql` on 3306
is *explicitly rejected* by that allowlist. Verified: there is no root
`conftest.py`, so `pytest e2e/` never loads the guard. Untouched safety guard,
no marker plumbing, no `-m "not perf and not browser"` edit to `pytest.ini`.

Mirrors the existing precedent in `docs/TESTING.md`:
`utils/parity/check_legacy_apis.py` lives outside `tests/` for exactly this
reason (hits live hosts, collides with the guard).

```
e2e/
  pytest.ini          # standalone config: no xdist, no -m filters, no --maxfail
  conftest.py         # base_url, logged-in page, error-capture fixtures
  test_console_sweep.py
  test_pr378_regressions.py
```

`e2e/pytest.ini` is **required**: running `pytest e2e/` from the root would
otherwise inherit `addopts = -n auto -m "not perf" --maxfail=5`, and `-n auto`
hard-errors if xdist isn't installed on the runner. Invoke as
`pytest -c e2e/pytest.ini e2e/`.

### 2.1 Fixtures (`e2e/conftest.py`)

- **`base_url`** — `pytest-playwright` already supplies `--base-url`; default to
  the `SAM_E2E_BASE_URL` env var, falling back to `http://localhost:5050` for
  local runs.
- **`errors(page)`** — the core of the net. Capture three channels:
  - `page.on("console")` filtered to `error` level
  - `page.on("pageerror")` (uncaught exceptions)
  - `page.add_init_script(...)` registering listeners for `htmx:targetError`,
    `htmx:swapError`, `htmx:responseError`, `htmx:sendError` into
    `window.__samErrors`, read back after each navigation.

  The injected listeners are belt-and-braces: htmx's console message is just the
  bare string `"htmx:targetError"` with no element info, while the **event
  detail carries the target selector** — the difference between "something broke
  on /status/derecho" and "`#editAllocationFormContainer` is missing". Register
  on `window` with capture (`add_init_script` runs before `document.body`
  exists; htmx events bubble body → document → window).
- **`admin_page`** — logs in via the stub form at `/auth/login`. Verified live:
  `:7050` (gunicorn/production target) serves the stub login and accepts any
  password. Prefer filling the username/password form over clicking a Quick
  Login button — the buttons are a dev affordance and a weaker contract. Reuse
  via `storage_state` so login happens once.
- **`ALLOWED_CONSOLE` ratchet** — regex allowlist for known-benign noise
  (favicon 404s etc.), asserted by equality like the CSP lint's
  `ALLOWED_VIOLATIONS`, so a fixed entry must also be removed.

### 2.2 `test_console_sweep.py` — the broad net

Parametrized over ~16 routes covering all four dashboard sections:

```
/user/accounts  /user/info
/status/derecho  /status/casper  /status/jupyterhub  /status/reservations
/allocations/projects  /allocations/transactions  /allocations/adjustments
/admin/projects  /admin/projects/directories  /admin/users-groups
/admin/resources  /admin/organizations  /admin/facilities  /admin/configuration
```

Per route: navigate, wait for network idle, click the affordances declared in a
short per-route map (~5 flows — **not** an exhaustive crawl; keep it fast and
stable), assert the captured error set is empty.

The declared flows must include the two that broke: `/admin/projects` search →
first result → first edit pencil, and `/status/derecho` legend → project modal →
first edit pencil.

### 2.3 `test_pr378_regressions.py` — explicit guards

- `#editAllocationModal` + `#editAllocationFormContainer` exist and the pencil
  loads a form with a visible Save button (bug #1).
- After a search-result click, the loaded card's header `bounding_box()['y']` is
  between 0 and ~40 — i.e. the title is on screen, not scrolled past (bug #2 —
  the assertion jsdom could never make).

### 2.4 Dependencies

New `e2e` extra in `pyproject.toml`, following the deferred-deps comment
convention already in `[project.optional-dependencies].test`:

```toml
e2e = ["pytest", "pytest-playwright"]
```

Deliberately **not** added to `test` — the 8-minute `sam-ci-docker` path must
not start installing browser libraries.

---

## Part 3 — CI wiring (`test-install.yaml` only)

`test-install.yaml` is the right host: it already runs `actions/checkout@v4` (so
`e2e/` is present), already does `make docker-up`, and already curls
`:7050/api/v1/health/live` and `:5050/api/v1/health/ready`. **No server startup
logic to add — only a browser.** It runs ~3.5 min and is not the critical path.

Insert after the existing health-curl steps, before `make docker-down`:

```yaml
- name: Install browser test deps
  run: |
    pip install pytest pytest-playwright
    playwright install --with-deps chromium

- name: Smoke — browser console sweep (port 7050; gunicorn / prod target)
  run: pytest -c e2e/pytest.ini e2e/ --base-url http://localhost:7050
```

Explicitly **not** touching `sam-ci-docker.yaml` (the canonical 8-min suite) or
`ci-staging.yaml`.

**Deferred: the `actions/cache` step.** The repo has zero caching in any workflow
today; `playwright install chromium` is a ~200–300 MB download costing ~30–60 s.
Measure the real added time on the first few runs before introducing the repo's
first cache step — don't pay that complexity up front.

**Flake budget.** E2E flake is the real long-term cost of this choice. Mitigate
by: no `--retries`, `expect`-style auto-waiting only (no bare `sleep`), and a
standing rule that a flaky sweep route gets *deleted or fixed*, never retried. If
flake becomes chronic, the fallback is demoting the step to
`continue-on-error: true` rather than letting people learn to ignore red.

---

## Verification

1. **Part 1 alone**, no browser:
   `pytest tests/unit/test_modal_shell_contract.py -v`. Then prove it bites —
   temporarily remove the `allocation_modals.html` include from
   `shared/project_details_modal.html` and confirm the test fails naming both
   the missing shell and its defining template. Restore.
2. **Part 2 locally** against the running dev stack:
   `pytest -c e2e/pytest.ini e2e/ --base-url http://localhost:5050`. Then prove
   the net works — re-introduce the same regression and confirm the sweep fails
   on `/status/derecho` with `htmx:targetError` and the
   `#editAllocationFormContainer` selector in the message. Restore.
3. **Full suite unaffected:** bare `pytest` must still collect ~3,100 tests and
   never touch `e2e/` (`testpaths = tests`). Confirm count and runtime unchanged.
4. **CI:** confirm `test-install.yaml` goes green with the new steps, noting the
   added wall-clock for the cache decision.

## Deliverables

- `tests/unit/test_modal_shell_contract.py` (replaces `test_modal_shell_pairing.py`)
- script-order assertion on `dashboards/base.html`
- `e2e/{pytest.ini,conftest.py,test_console_sweep.py,test_pr378_regressions.py}`
- `pyproject.toml` — new `e2e` extra
- `.github/workflows/test-install.yaml` — two new steps
- `docs/TESTING.md` — a browser-tier section: why it lives outside `tests/`, how
  to run it locally, and the flake rule

## Handoff

Start the implementation session on the `frontend_test_net` branch with this
doc. Work the Progress checklist top-down, ticking items and pushing to the same
PR as each part lands. Parts 1 and 2 are independent — Part 1 is shippable on
its own if Part 2 stalls on flake.
