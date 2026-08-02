# Front-end Regression Net (and why not a JS unit harness)

**Status: PLANNED — not started.**
Branch: `ci_playwright`, cut fresh from `staging` (docs-only commit first;
implementation lands on the same branch). Targets `staging`.

> Deliverable of *this* commit: this document only. `docs/**` is in the
> `paths-ignore` list for `sam-ci-docker.yaml`, `sam-ci-conda_make.yaml` and
> `test-install.yaml`, and `mega-linter.yaml` only triggers on PRs to
> `main`/`master` — so a docs-only PR to `staging` runs essentially no CI.
> No `[skip ci]` needed.

> **Revision note.** An earlier draft of this plan (PR #380, branch
> `frontend_test_net`) was written before **PR #406** migrated CI off the
> deprecated Node 20 Actions runtime. #406 rewrote every action pin
> (`checkout@v4`→`@v7` and 16 more), added `.github/dependabot.yml`, added
> `.github/scripts/`, introduced the `_`-prefixed local-reusable-workflow
> convention, and established *actionlint-clean* as the bar for any workflow
> file we touch. Part 3 below is written against post-#406 CI. Two other
> corrections carried over from review: the sweep's route list was wrong
> (§2.2) and the rate limiter will 429 the sweep unless CI raises the ceiling
> (§3.2).

## Progress

- [ ] Step 0 — this doc committed, PR opened against `staging`
- **Part 1 — Python shell-contract guards (no new dependencies)**
  - [ ] 1.1 `tests/unit/test_modal_shell_contract.py` (generalizes the
        `test_modal_shell_pairing.py` added in PR #378)
  - [ ] 1.2 script-order assertion on `dashboards/base.html`
  - [ ] 1.3 prove it bites — remove the `allocation_modals.html` include,
        confirm a named failure, restore
- **Part 2 — Playwright console sweep**
  - [ ] 2.1 `e2e/` skeleton: `pytest.ini`, `conftest.py` (base_url, error
        capture, admin login)
  - [ ] 2.2 `test_console_sweep.py` — 22 routes (derived) + ~5 declared flows
  - [ ] 2.3 `test_pr378_regressions.py` — the two bugs from PR #378
  - [ ] 2.4 `pyproject.toml` — new pinned `e2e` extra; `Makefile` — `make e2e`
  - [ ] 2.5 prove the net works — re-introduce the dead pencil, confirm the
        sweep names `#editAllocationFormContainer`, restore
- **Part 3 — CI**
  - [ ] 3.1 new `.github/workflows/browser-smoke.yaml`
  - [ ] 3.2 raise `RATELIMIT_AUTHED` for the CI stack
  - [ ] 3.3 `actionlint` clean on the new workflow
  - [ ] 3.4 confirm bare `pytest` still collects ~3,100 and never touches `e2e/`
  - [ ] 3.5 measure added wall-clock; decide on `actions/cache` (deferred by default)
- [ ] `CLAUDE.md` — add `browser-smoke` to the `[skip ci]` workflow list
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
any of the ~3,000 lines of JavaScript in `src/webapp/static/js/`.

### Why not a JS unit harness (vitest/jsdom) — rejected

- The overwhelming majority of that JS is htmx-swap/DOM lifecycle glue. The
  genuinely pure, unit-testable logic is a couple of hundred lines, and **none
  of it is exported** — every file in `static/js/` is an ES5 IIFE with no module
  system, so `compareKeys`, `dedupeSlashes`, `toLocalNaiveISO` et al. are
  unreachable from outside today.
- **jsdom zeroes out `getBoundingClientRect()`**, so bug #2 above is untestable
  there by construction — the exact class of bug that prompted the question.
- It would be the repo's first node dependency, in a codebase that hand-vendors
  Bootstrap/htmx/jQuery with SRI pins **specifically** to keep npm out of the
  serving path (`src/webapp/static/vendor/README.md`). PR #406's audit
  confirmed the repo has no Node config of its own at all — no `setup-node`,
  no `package.json`, no `.nvmrc`.

Cost high, catch rate low, philosophy backwards. Revisit only if the JS ever
grows real exported domain logic. The one honourable mention: `path-preview.js`
deliberately mirrors the server's `_assemble_directory_name()`
(`dashboards/admin/projects_routes.py:2225`), so a divergence there is a real
silent bug class — but one worth covering with a single end-to-end assertion,
not a toolchain.

### What we build instead

Two cheap, complementary layers, both driven by pytest:

| Layer | Catches | Cost |
|---|---|---|
| Python shell-contract guards | dead `data-bs-target` shells (**silent in the browser**) | no new deps, ~0s in the existing suite |
| Playwright console sweep | dead `hx-target`, JS exceptions, script-order breaks | one dev dep, ~60–90s on its own workflow |

The complementarity is the point, and was **verified empirically against the
live app**: a dangling `hx-target` *does* emit `console.error("htmx:targetError")`
in htmx 2.0.4, so the browser sweep catches it — but a dangling
`data-bs-target` produces **no console output at all** (htmx's `logger` defaults
to `null`, and `htmx-config.js` listens for `responseError`/`sendError`, never
`targetError`). PR #378's pencil had both; a Bootstrap-only affordance would
slip past a browser-only net.

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

Uses `auth_client` (logged in as `benkirk`, the admin-equivalent identity —
`tests/conftest.py:317`) and the `active_project` fixture. Keep the existing
`test_edit_project_page_ships_one_of_each` case.

**Make the page list self-maintaining.** The seed test's comment says its
explicit `PAGES_WITH_PROJECT_MODAL` list is "kept explicit rather than derived
from the route map: the point is to catch a new page that includes one fragment
and forgets the other" — but a hand-maintained list catches a new page only if
somebody remembers to add it. Invert it: walk `src/webapp/templates/` for
`{% include %}` of `shared/project_details_modal.html`, and assert the including
set *equals* the pinned list. A new page that includes the fragment then
**forces** a test update instead of silently escaping coverage.

**Precedent to follow:** `tests/unit/test_template_csp_lint.py` — same shape
(explicit registry, exact-equality ratchet, docstring explaining what to do
instead when it fires).

### 1.2 Script-order guard (~10 lines)

`actions.js` defines `window.registerAction` / `window.revealCard`, and five
later scripts call them at eval time. The order in
`src/webapp/templates/dashboards/base.html:219-234` is load-bearing and
undeclared — reordering silently breaks every `data-action` on every page.
Assert `actions.js` appears before `pickers.js`, `dashboard-init.js`,
`admin-cards.js`, `modals.js`, and `form-helpers.js`.

---

## Part 2 — Playwright console sweep

### Placement: `e2e/` at the repo root, **outside** `tests/`

A deliberate design decision, not tidiness. `tests/conftest.py`'s
`pytest_configure` safety guard hard-exits (code 2) unless `SAM_TEST_DB_URL`
points at an allowlisted `(host, port)` — `(127.0.0.1, 3307)`,
`(localhost, 3307)`, `(mysql-test, 3306)`. Browser tests talk HTTP, never the
DB, and the compose `mysql` on 3306 is *explicitly rejected* by that allowlist.
Verified: there is no root `conftest.py`, and conftests are only collected from
a test file's ancestor directories — `e2e/` is a sibling of `tests/`, so
`pytest e2e/` never loads the guard. Untouched safety guard, no marker
plumbing, no `-m "not perf and not browser"` edit to `pytest.ini`.

Mirrors the existing precedent in `docs/TESTING.md:303-309`:
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
hard-errors if xdist isn't installed on the runner. Invoke via `make e2e`
(§2.4), which wraps `pytest -c e2e/pytest.ini e2e/`.

Nothing under `e2e/` may import from `sam`, `webapp`, or `system_status`. That
is not a style rule — it is what lets CI skip the conda build entirely (§3.1).

### 2.1 Fixtures (`e2e/conftest.py`)

- **`base_url`** — `pytest-playwright` already supplies `--base-url`; default to
  the `SAM_E2E_BASE_URL` env var, falling back to `http://localhost:7050`.
- **`errors(page)`** — the core of the net. Capture three channels:
  - `page.on("console")` filtered to `error` level
  - `page.on("pageerror")` (uncaught exceptions)
  - `page.add_init_script(...)` registering listeners for `htmx:targetError`
    and `htmx:swapError` into `window.__samErrors`, read back after each
    navigation.

  The injected listeners are belt-and-braces: htmx's console message is just the
  bare string `"htmx:targetError"` with no element info, while the **event
  detail carries the target selector** — the difference between "something broke
  on /status/derecho" and "`#editAllocationFormContainer` is missing". Register
  on `window` with capture (`add_init_script` runs before `document.body`
  exists; htmx events bubble body → document → window, and `htmx-config.js`
  itself binds on `document.body`).

  **Do not put `htmx:responseError` / `htmx:sendError` in the failing set.**
  Every non-2xx from any hx-request lands there — including legitimate 4xx and
  every 429 from the rate limiter (§3.2). If they are captured at all, capture
  them to a separate reporting channel or allowlist by status code.
- **`admin_page`** — logs in via the stub form at `/auth/login` as **`benkirk`**,
  the one username that is both admin-equivalent
  (`USER_PERMISSION_OVERRIDES['benkirk']`, `webapp/utils/rbac.py:215`) and
  preserved verbatim by the obfuscated snapshot (every other username is
  rewritten to `user_<hex>`). Same identity as the `auth_client` fixture, so
  Parts 1 and 2 agree on who they are testing as.

  Verified live: `:7050` serves the stub login and accepts any password. It runs
  **DevelopmentConfig** — compose sets no `FLASK_CONFIG` and
  `webapp/config.py:384` defaults to `development` — so both the username form
  and the Quick Login buttons are present. (`ProductionConfig.validate()`
  hard-fails unless `AUTH_PROVIDER=oidc`, so `:7050` can never be production
  here.) Prefer filling the form over clicking a Quick Login button — the
  buttons are a dev affordance and a weaker contract. Reuse via `storage_state`
  so login happens once; the login POST is separately rate-limited at
  `5 per minute`.
- **`ALLOWED_CONSOLE` ratchet** — regex allowlist for known-benign noise
  (favicon 404s etc.), asserted by equality like the CSP lint's
  `ALLOWED_VIOLATIONS`, so a fixed entry must also be removed.

### 2.2 `test_console_sweep.py` — the broad net

**Derive the route list; do not hand-write it.** An earlier draft's hand-written
list invented `/status/reservations` (which has never existed — `reservations`
is a template variable in `dashboards/status/blueprint.py`, not a route) and
omitted seven real pages, including every one added most recently: `/user/data`,
`/user/jobs`, `/status/events`, `/status/filesystem-scans`,
`/status/job-history`, `/admin/contracts`, `/admin/expirations`. Those are the
most JS-heavy surfaces in the app — exactly where the net has the most to catch.

`tests/unit/snapshots/dashboard_route_map.json` is already the pinned source of
truth for dashboard routing (`tests/unit/test_route_map_parity.py`, regenerated
with `ROUTE_MAP_REGEN=1`). It is a flat list of `[endpoint, rule, [methods]]`
triples, so the sweep parametrizes off it directly:

```python
SKIP = {
    '/user/', '/status/', '/allocations/', '/admin/',   # tab-index redirects
    '/admin/impersonate', '/admin/stop-impersonating',  # session-mutating
    '/admin/expirations/export',                        # CSV download
    '/allocations/cache/status',                        # JSON diagnostic
}
PAGES = sorted({
    rule for _endpoint, rule, methods in json.load(open(ROUTE_MAP))
    if 'GET' in methods and '<' not in rule
    and '/htmx/' not in rule and not rule.endswith('_fragment')
    and rule not in SKIP
})
```

That yields **22 routes** today, and a new top-level tab enters the sweep
automatically instead of being forgotten:

```
/admin/configuration   /admin/contracts    /admin/expirations   /admin/facilities
/admin/organizations   /admin/projects     /admin/projects/directories
/admin/resources       /admin/users-groups
/allocations/adjustments   /allocations/projects   /allocations/transactions
/status/casper   /status/derecho   /status/events   /status/filesystem-scans
/status/job-history   /status/jupyterhub
/user/accounts   /user/data   /user/info   /user/jobs
```

Per route: navigate, wait for network idle, click the affordances declared in a
short per-route map (~5 flows — **not** an exhaustive crawl; keep it fast and
stable), assert the captured error set is empty.

The declared flows must include the two that broke: `/admin/projects` search →
first result → first edit pencil, and `/status/derecho` legend → project modal →
first edit pencil.

**Plugin-gated routes render empty in CI.** `/user/jobs`, `/status/job-history`,
`/status/filesystem-scans` and `/user/data` hide their tabs when the
hpc-usage-queries / fs-scans plugins aren't warm
(`dashboards/user/blueprint.py:97,109`; `dashboards/status/blueprint.py:79,85`).
Keep them in the sweep — a shell-level JS break is still caught — but be honest
that their charts are exercised only by a **local** run against the dev stack,
not by CI.

### 2.3 `test_pr378_regressions.py` — explicit guards

- `#editAllocationModal` + `#editAllocationFormContainer` exist and the pencil
  loads a form with a visible Save button (bug #1).
- After a search-result click, the loaded card's header `bounding_box()['y']` is
  between 0 and ~40 — i.e. the title is on screen, not scrolled past (bug #2 —
  the assertion jsdom could never make).

### 2.4 Dependencies and invocation

New `e2e` extra in `pyproject.toml`. It is load-bearing, not decorative: CI
installs `.[e2e]` (§3.1), mirroring `sam-ci-conda_make.yaml`'s existing
`pip install -e ".[test]"`.

```toml
e2e = [
    "pytest",
    # Browser sweep only (e2e/) — deliberately NOT in [test]: the 8-minute
    # sam-ci-docker path must not install browser libraries. Pinning the
    # driver also pins the Chromium build `playwright install` fetches.
    #
    # .github/dependabot.yml is scoped to github-actions on purpose ("Python
    # dependencies … are a separate decision"), so this pin is hand-bumped;
    # widen it when a Playwright major lands.
    "pytest-playwright>=0.7,<0.8",
]
```

> Editing `pyproject.toml` rebuilds the hash-keyed conda env — expect the
> rebuild, and don't `git stash -u` around it.

CI and local devs share one invocation via a `Makefile` target. Note the
deliberate absence of `$(config_env)` / `source etc/config_env.sh`, unlike
`check:` and `perf:` — the sweep imports nothing from `sam`, and that is what
lets CI skip the conda build:

```makefile
SAM_E2E_BASE_URL ?= http://localhost:7050

e2e: ## Run the Playwright browser console sweep against a running stack
	python3 -m pytest -c e2e/pytest.ini e2e/ --base-url $(SAM_E2E_BASE_URL)
```

Local run against the dev server: `make e2e SAM_E2E_BASE_URL=http://localhost:5050`.

---

## Part 3 — CI: a new `browser-smoke.yaml`

### 3.1 Why a standalone workflow

The obvious hosts are already spoken for, and both say so in their own
comments. `test-install.yaml` exists to prove the piped-install path is healthy
("The full pytest suite is intentionally NOT run here"); `sam-ci-conda_make.yaml`
exists to prove the conda/pip install path produces a working environment ("the
pytest suite does NOT run here"). A front-end regression turning either of them
red points reviewers at the installer or the conda env. It would also mean
editing `test-install.yaml`, which carries pre-existing actionlint SC2086/SC2129
findings — a new file is born clean.

So: a new directly-triggered workflow, modeled structurally on
`sam-ci-conda_make.yaml` (runner-side Python → live stack → smoke steps →
teardown), but swapping `conda-incubator/setup-miniconda@v4` for
`actions/setup-python@v6`. Seconds instead of minutes, and correct precisely
because `e2e/` imports nothing from `sam`.

Conventions it must match, all shared by the eleven existing workflows:

- `on:` = `push: [main]` + `pull_request: [main, staging]` with
  `paths-ignore: ['docs/**', '**.md']` + `workflow_dispatch:`
- the same six-line `[skip ci] / [ci skip] / [no ci]` `if:` guard
- `concurrency: ${{ github.ref }}-${{ github.workflow }}`, `cancel-in-progress: true`
- `actions/checkout@v7` with `lfs: true`; `docker/setup-docker-action@v5`
- `make docker-down` under `if: always()`
- **no `_` prefix** — post-#406 that prefix means "reusable workflow"
  (`_prune-workflow-runs.yaml`); this one is triggered directly

Every action pin here is one #406 already uses, so dependabot's weekly grouped
`github-actions` PR maintains them. `setup-python@v6` is the single new pin and
joins the same group.

```yaml
      - uses: actions/checkout@v7
        with:
          lfs: true

      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'

      - uses: docker/setup-docker-action@v5
        with:
          docker-ce-version: latest

      - name: Prepare .env            # mirrors sam-ci-conda_make.yaml
        run: |
          cp .env.example .env
          echo "RATELIMIT_AUTHED=10000 per minute" >> .env   # see 3.2

      # Before `make docker-up` so a dependency failure fails fast
      # instead of after a full stack start.
      - name: Install browser test deps
        run: |
          pip install -e ".[e2e]"
          playwright install --with-deps chromium

      - name: Start the stack
        run: make docker-up

      - name: Browser console sweep (port 7050; gunicorn / prod target)
        run: make e2e

      - name: Cleanup
        if: always()
        run: make docker-down
```

`pip install -e ".[e2e]"` does pull the project's full base dependency set
(matplotlib, cryptography, psycopg2-binary, …) that `e2e/` never imports — all
pure-wheel on linux/cp313, so tens of seconds against a job whose long pole is
`make docker-up`. Accepted deliberately: it matches
`sam-ci-conda_make.yaml`'s existing `pip install -e ".[test]"`, and the
alternative — naming `pytest-playwright==…` inline — would duplicate the pin in
YAML where dependabot doesn't watch it either, and let the two drift.

Explicitly **not** touching `sam-ci-docker.yaml` (the canonical 8-min suite),
`sam-ci-conda_make.yaml`, `test-install.yaml`, or `ci-staging.yaml`.

### 3.2 The rate limiter will 429 the sweep

`webapp/limiter/__init__.py:73` installs a **global** `default_limits` of
`RATELIMIT_AUTHED` — **200/min**, `fixed-window`, Redis-backed in compose and
therefore shared across gunicorn workers. `_key_func()` resolves to
`user:benkirk` for the entire sweep, so all 22 routes plus their fragment loads
plus five click-flows spend one bucket. Nothing calls `limiter.exempt`,
including the `static` endpoint.

A 429 arrives as `htmx:responseError` — indistinguishable from a real
regression, and arriving in bursts, which is the worst possible flake shape.

Raise the ceiling rather than disabling the limiter, so its code path stays
under test. `compose.yaml` pins only `RATELIMIT_STORAGE_URI` (line 63), so
`RATELIMIT_AUTHED` flows through `env_file: .env` — hence the `echo` in the
`Prepare .env` step above. Do **not** solve this by pacing the sweep.

### 3.3 Acceptance bar and deferred work

**`actionlint` reports zero findings on the new workflow file** — the standard
#406 set for itself. It isn't installed locally:

```bash
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:latest -color
```

**Deferred: the `actions/cache` step.** The repo has zero caching in any
workflow today; `playwright install chromium` is a ~200–300 MB download costing
~30–60 s. #406 raised the CI-hygiene bar (dependabot, pinned trufflehog), but
caching is still a complexity trade to make with a number in hand — measure the
real added time on the first few runs first.

**Flake budget.** E2E flake is the real long-term cost of this choice. Mitigate
by: no `--retries`, `expect`-style auto-waiting only (no bare `sleep`), and a
standing rule that a flaky sweep route gets *deleted or fixed*, never retried.
If flake becomes chronic, the fallback is `continue-on-error: true` on this
workflow alone — which is cheap precisely because it is its own workflow —
rather than letting people learn to ignore red.

---

## Verification

1. **Part 1 alone**, no browser:
   `pytest tests/unit/test_modal_shell_contract.py -v`. Then prove it bites —
   temporarily remove the `allocation_modals.html` include from
   `shared/project_details_modal.html` and confirm the test fails naming both
   the missing shell and its defining template. Restore.
2. **Part 2 locally** against the running dev stack:
   `make e2e SAM_E2E_BASE_URL=http://localhost:5050`. Then prove the net works —
   re-introduce the same regression and confirm the sweep fails on
   `/status/derecho` with `htmx:targetError` and the
   `#editAllocationFormContainer` selector in the message. Restore.
3. **Confirm §3.2 is real** before trusting the mitigation: with the stack up,
   log in as `benkirk`, hit ~10 dashboards quickly, and watch
   `X-RateLimit-Remaining` (the limiter sets `headers_enabled=True`) approach
   zero in devtools.
4. **Full suite unaffected:** bare `pytest` must still collect ~3,100 tests and
   never touch `e2e/` (`testpaths = tests`). Confirm count and runtime unchanged.
5. **CI:** `actionlint` clean; `browser-smoke.yaml` goes green on the PR (it
   fires on PRs to `staging`, so no dispatch is needed to verify it); note the
   wall-clock for the cache decision.

## Deliverables

- `tests/unit/test_modal_shell_contract.py` (replaces `test_modal_shell_pairing.py`)
- script-order assertion on `dashboards/base.html`
- `e2e/{pytest.ini,conftest.py,test_console_sweep.py,test_pr378_regressions.py}`
- `pyproject.toml` — new pinned `e2e` extra
- `Makefile` — `e2e` target
- `.github/workflows/browser-smoke.yaml` — new
- `CLAUDE.md` — add `browser-smoke` to the `[skip ci]` workflow list
- `docs/TESTING.md` — a browser-tier section: why it lives outside `tests/`, how
  to run it locally via `make e2e`, and the flake rule

## Handoff

Start the implementation session on the `ci_playwright` branch with this doc.
Work the Progress checklist top-down, ticking items and pushing to the same PR
as each part lands. Parts 1 and 2 are independent — Part 1 is shippable on its
own if Part 2 stalls on flake.

If PR #380 (branch `frontend_test_net`) is still open, close it in favour of
this branch: it is 8 commits behind `staging` and its Part 3 predates #406.
