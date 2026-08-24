"""Fixtures for the browser tier.

Nothing here imports ``sam`` / ``webapp`` / ``system_status`` — the suite drives
a *running* stack over HTTP. That boundary is load-bearing: it is why CI can
install a bare Python plus ``pytest-playwright`` instead of building the conda
environment, and why `tests/conftest.py`'s DB safety guard is never involved.

See ``docs/plans/implemented/FRONTEND_TEST_NET.md``.
"""
import json
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTE_MAP = REPO_ROOT / 'tests' / 'unit' / 'snapshots' / 'dashboard_route_map.json'

# The one username that is both admin-equivalent (USER_PERMISSION_OVERRIDES in
# webapp/utils/rbac.py grants it every Permission) and preserved verbatim by the
# obfuscated snapshot — every other username is rewritten to user_<hex>. Same
# identity the Python tier's `auth_client` fixture uses, so both tiers test as
# the same person. The stub auth provider accepts any non-empty password.
ADMIN_USERNAME = os.environ.get('SAM_E2E_USER', 'benkirk')
ADMIN_PASSWORD = 'browser-smoke'

# Routes that `abort(404)` when their optional plugin isn't warm — see
# `my_data()` / `my_jobs()` in webapp/dashboards/user/blueprint.py. CI runs
# without the hpc-usage-queries / fs-scans plugins, so 404 there is the designed
# response. A 404 on any other route is a failure.
PLUGIN_GATED = {'/user/data', '/user/jobs'}

# Console noise that is known-benign.
#
# Deliberately EMPTY. The sweep currently runs every dashboard page with zero
# console output, so there is nothing to excuse — and an empty allowlist is the
# strongest form of this ratchet. (The obvious candidates, favicon 404s, never
# fire: the app serves one.) `test_console_allowlist_has_no_dead_entries` in
# test_console_sweep.py keeps any future addition honest — a pattern that stops
# matching has to be deleted rather than left to rot, exactly like
# ALLOWED_VIOLATIONS in tests/unit/test_template_csp_lint.py.
ALLOWED_CONSOLE = ()


def dashboard_page_routes():
    """Every top-level dashboard page, derived from the pinned route map.

    `tests/unit/snapshots/dashboard_route_map.json` is already the source of
    truth for dashboard routing (tests/unit/test_route_map_parity.py pins it;
    ROUTE_MAP_REGEN=1 regenerates it). Deriving from it means a new tab enters
    the sweep automatically — an earlier hand-written list in the plan doc
    invented a route that never existed while omitting seven real pages.
    """
    skip = {
        # Section landing routes — they redirect to the default tab, which the
        # sweep already visits directly.
        '/user/', '/status/', '/allocations/', '/admin/',
        # Session-mutating; not a page.
        '/admin/impersonate', '/admin/stop-impersonating',
        # File download, not an HTML page.
        '/admin/expirations/export',
        # JSON diagnostic endpoint.
        '/allocations/cache/status',
    }
    rows = json.loads(ROUTE_MAP.read_text())
    return sorted({
        rule for endpoint, rule, methods in rows
        if 'GET' in methods
        and '<' not in rule                 # no URL converters
        and '/htmx/' not in rule            # htmx fragment, not a page
        # WARNING: Classify on the ENDPOINT, not the rule. A fragment view is named
        # `*_fragment` by convention, but its URL need not be: `/admin/expirations`
        # is `expirations_fragment`, and `/allocations/xras_remediations` is
        # `xras_remediations_fragment`. Keying off the rule meant each such route
        # had to be named in `skip` by hand, one incident at a time — the first
        # was found when `assert_theme_applied` reported `data-bs-theme` was None,
        # which is exactly right for a fragment and impossible for a page, and the
        # second the same way when the XRAS Remediations card landed.
        #
        # Before that, both were swept as pages and PASSED, because a fragment
        # emits no console errors — so the sweep was reporting coverage it did
        # not have. Endpoint-based classification closes the class instead of
        # chasing names.
        and not endpoint.endswith('_fragment')
        and rule not in skip
    })


# Registers listeners for the htmx error events whose console output is
# useless. htmx logs the bare string "htmx:targetError" with no element
# information, while the *event detail* carries the offending selector — the
# difference between "something broke on /status/derecho" and
# "#editAllocationFormContainer is missing".
#
# Bound on `window` with capture: add_init_script runs before document.body
# exists, and htmx events bubble body -> document -> window.
#
# htmx:responseError / htmx:sendError are deliberately NOT captured. Every
# non-2xx from any hx-request lands there, including legitimate 4xx and every
# 429 from the rate limiter, which would make the sweep fail for reasons that
# have nothing to do with the front end.
_ERROR_TRAP = """
window.__samErrors = window.__samErrors || [];
['htmx:targetError', 'htmx:swapError'].forEach(function (name) {
    window.addEventListener(name, function (evt) {
        var detail = evt.detail || {};
        // For targetError the detail carries the unresolved selector string.
        var missing = typeof detail.target === 'string'
            ? detail.target
            : (detail.target && detail.target.id ? '#' + detail.target.id : '(unknown)');
        var elt = evt.target && evt.target.outerHTML
            ? evt.target.outerHTML.replace(/\\s+/g, ' ').slice(0, 200)
            : '(unknown element)';
        window.__samErrors.push(name + ' -> ' + missing + '   from: ' + elt);
    }, true);
});
"""


def _is_report_only_csp(text: str) -> bool:
    """A browser **report-only** CSP violation — advisory, never a page failure.

    Dropped at the source rather than added to ``ALLOWED_CONSOLE`` for two
    reasons. First, report-only is benign *by construction*: the browser logs
    the report and takes "no further action", so the page behaves exactly as it
    would with a clean console — which is all the sweep asks. Second, the
    concrete case is a **third party**: ``/status/events`` embeds a Google
    Calendar, and ``calendar.google.com`` serves its own report-only
    ``frame-ancestors 'self'`` — a header SAM cannot change and a frame that
    still loads. An allowlist entry would fail the no-dead-entries ratchet
    anyway, which only visits ``/user/accounts`` and ``/admin/projects``, so the
    pattern would match nothing and read as dead.

    Scoped tightly to report-only so an *enforced* CSP violation — one where the
    browser actually blocked something — still fails the sweep loudly.
    """
    low = (text or '').lower()
    return 'report-only' in low and 'content security policy' in low


class ErrorCollector:
    """Captures the three channels a broken front end actually speaks through."""

    def __init__(self, page):
        self.page = page
        self.console = []
        self.exceptions = []
        page.on('console', self._on_console)
        page.on('pageerror', lambda exc: self.exceptions.append(str(exc)))

    def _on_console(self, message):
        if message.type == 'error' and not _is_report_only_csp(message.text):
            self.console.append(message.text)

    def htmx(self):
        """Read back the injected htmx error listeners."""
        try:
            return self.page.evaluate('window.__samErrors || []')
        except Exception:                       # page navigated away / closed
            return []

    def drain(self):
        """All failures worth reporting, with the allowlist applied."""
        found = [
            f'console.error: {text}' for text in self.console
            if not any(pattern.search(text) for pattern in ALLOWED_CONSOLE)
        ]
        found += [f'uncaught exception: {exc}' for exc in self.exceptions]
        found += [f'htmx event: {entry}' for entry in self.htmx()]
        return found

    def clear(self):
        self.console.clear()
        self.exceptions.clear()
        try:
            self.page.evaluate('window.__samErrors = []')
        except Exception:
            pass


@pytest.fixture(scope='session')
def base_url(request):
    """Where the stack under test is listening.

    `--base-url` (pytest-base-url, pulled in by pytest-playwright) wins; then
    SAM_E2E_BASE_URL; then the compose `webapp` service, which is the
    gunicorn/production target and therefore the more honest thing to smoke.
    """
    return (
        request.config.getoption('--base-url', default=None)
        or os.environ.get('SAM_E2E_BASE_URL')
        or 'http://localhost:7050'
    )


@pytest.fixture(scope='session')
def storage_state(browser, base_url, tmp_path_factory):
    """Log in once for the whole session and reuse the cookie.

    Logging in per test would hit the login rate limit (RATELIMIT_AUTH_LOGIN
    defaults to '5 per minute'). Fills the real form rather than clicking a
    Quick Login button — the buttons are a dev affordance and a weaker
    contract, and they only exist under DevelopmentConfig.
    """
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    response = page.goto('/auth/login')
    assert response is not None, f'no response from {base_url}/auth/login'
    assert response.status == 200, (
        f'{base_url}/auth/login returned {response.status}. Is the stack up '
        f'(`make docker-up`), and is it serving the stub login? A production '
        f'config would serve an OIDC redirect instead.'
    )

    page.fill('#username', ADMIN_USERNAME)
    page.fill('#password', ADMIN_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state('domcontentloaded')

    assert '/auth/login' not in page.url, (
        f'login as {ADMIN_USERNAME} did not leave the login page. '
        f'The obfuscated snapshot must preserve that username.'
    )

    state_path = tmp_path_factory.mktemp('e2e-auth') / 'storage_state.json'
    context.storage_state(path=str(state_path))
    context.close()
    return str(state_path)


@pytest.fixture(scope='session')
def browser_context_args(browser_context_args, base_url, storage_state):
    return {
        **browser_context_args,
        'base_url': base_url,
        'storage_state': storage_state,
        'viewport': {'width': 1440, 'height': 1000},
    }


@pytest.fixture
def errors(page):
    """Attach the error traps before anything navigates."""
    page.add_init_script(_ERROR_TRAP)
    return ErrorCollector(page)


def expand_first_project_card(page):
    """Open the first project accordion on /user/accounts and return a pencil.

    The project cards render collapsed, so their per-allocation edit pencils are
    in the DOM but not clickable. Skips (rather than fails) when the dataset has
    no expandable project — a skip here is a data gap, not a regression.
    """
    header = page.locator('[data-bs-toggle="collapse"][data-bs-target^="#project-"]:visible').first
    try:
        header.wait_for(state='visible', timeout=10_000)
    except Exception:
        pytest.skip('no project cards rendered for this user')

    target = header.get_attribute('data-bs-target')
    header.click()
    page.locator(f'{target}.show').wait_for(state='visible', timeout=10_000)
    page.wait_for_load_state('networkidle')

    pencil = page.locator(
        f'{target} [data-bs-target="#editAllocationModal"]:visible').first
    if pencil.count() == 0:
        pytest.skip(f'no editable allocation inside {target} in this dataset')
    return pencil


#: Must match `webapp.utils.htmx.THEME_COOKIE`. Dark mode is rendered
#: server-side from this cookie onto `<html data-bs-theme>`, so pinning it
#: here is the whole of what a browser-tier theme switch needs — no clicking,
#: no reload dance.
THEME_COOKIE = 'sam_theme'
THEMES = ('light', 'dark')


def set_theme(page, base_url, theme):
    """Pin the server-rendered theme for this page's browsing context.

    Set on the context rather than by clicking the toggle: the toggle's own
    behavior is unit-tested (tests/unit/test_theme_transport.py), and driving
    it here would add a reload to every parameterized case for no extra
    coverage.
    """
    page.context.add_cookies(
        [{'name': THEME_COOKIE, 'value': theme, 'url': base_url}])


def assert_theme_applied(page, theme):
    """The server really did render the theme we asked for.

    Without this a theme-parameterized test can pass twice against the same
    light page — the cookie is silently dropped and nothing complains.
    """
    actual = page.evaluate(
        "document.documentElement.getAttribute('data-bs-theme')")
    assert actual == theme, (
        f'asked for {theme!r} but <html data-bs-theme> is {actual!r} — the '
        f'{THEME_COOKIE} cookie is not reaching the server, so this test is '
        f'not exercising what it claims to')


def visit(page, url):
    """Navigate and settle. Returns the response, or None for a skipped route."""
    response = page.goto(url)
    assert response is not None, f'no response for {url}'
    if response.status == 404 and url in PLUGIN_GATED:
        pytest.skip(f'{url} is plugin-gated and its plugin is not loaded')
    assert response.status == 200, f'{url} returned HTTP {response.status}'
    try:
        page.wait_for_load_state('networkidle', timeout=15_000)
    except Exception:
        # A page with a long-poll or a slow fragment never goes idle; the
        # error assertions below are still valid on what did load.
        pass
    return response
