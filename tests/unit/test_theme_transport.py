"""How ``theme=dark`` gets from the browser to the server, and onto the page.

The sibling of ``test_layout_transport.py``, and deliberately the simpler of
the two. Both axes exist for the same reason: a rendering mode the server must
know *before* it renders, because charts are matplotlib SVGs with baked-in
colors that no stylesheet can retheme.

**One channel, not two.** ``layout-axis.js`` writes a cookie *and* injects
``?layout=`` into every htmx request, because a viewport is discovered
client-side after the server has already answered. A theme is never
discovered — it is declared by a click that then reloads, so the cookie and
the browser can never disagree. ``?theme=`` is honored for hand-debugging but
nothing sets it, which is why there is no ``configRequest`` half to this file.

The payoff is that the theme is correct in the **first byte of HTML**: there is
no flash to prevent, no ``<head>`` script for the nonce-free CSP to forbid,
and no ``localStorage`` read racing the paint.
"""

import re
from pathlib import Path

import pytest

from webapp.extensions import user_aware_cache_key
from webapp.utils.htmx import (LAYOUT_COOKIE, THEME_COOKIE, _THEMES,
                               read_layout, read_theme)

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'static'
TEMPLATES = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'
JS = STATIC / 'js' / 'theme-toggle.js'


# --------------------------------------------------------------------------
# read_theme
# --------------------------------------------------------------------------

class TestReadTheme:

    def test_defaults_to_light(self, app):
        with app.test_request_context('/'):
            assert read_theme() == 'light'

    @pytest.mark.parametrize('value', ['dark', 'DARK', ' dark ', 'light'])
    def test_cookie_selects_a_declared_theme(self, app, value):
        with app.test_request_context(
                '/', headers={'Cookie': f'{THEME_COOKIE}={value}'}):
            assert read_theme() == value.strip().lower()

    @pytest.mark.parametrize('value', ['dark', 'light'])
    def test_query_string_works_for_hand_debugging(self, app, value):
        """``?theme=dark`` is a debugging affordance only — no JavaScript ever
        sets it. It is kept so this function stays literally the same function
        as ``read_layout``, which is the property a reviewer should check."""
        with app.test_request_context('/', query_string={'theme': value}):
            assert read_theme() == value

    def test_query_string_outranks_the_cookie(self, app):
        """Same precedence as ``read_layout``, so the two stay readable side
        by side. Reachable only by hand, since nothing injects the param."""
        with app.test_request_context(
                '/', query_string={'theme': 'dark'},
                headers={'Cookie': f'{THEME_COOKIE}=light'}):
            assert read_theme() == 'dark'

    #: ``auto`` is in this list deliberately: it is the obvious future value
    #: (§ *auto* in docs/plans/implemented/DARK_MODE.md defers it), and until it is really
    #: implemented it must degrade to light rather than reach a cache key.
    JUNK = ['midnight', '', 'DARKMODE', '1', 'auto', 'dark mode',
            'light);--', 'dark dark']

    @pytest.mark.parametrize('bad', JUNK)
    def test_unknown_cookie_values_fall_back(self, app, bad):
        """Lenient, never a 400 — matching ``read_layout``. A stale cookie must
        not break a page.

        Note a trailing ``;`` is *not* junk at this layer: in a Cookie header
        it is the pair separator, so ``sam_theme=dark;`` delivers ``dark``.
        That case belongs to the query string, below."""
        with app.test_request_context(
                '/', headers={'Cookie': f'{THEME_COOKIE}={bad}'}):
            assert read_theme() == 'light'

    @pytest.mark.parametrize('bad', JUNK + ['dark;', 'dark&theme=light'])
    def test_unknown_query_values_fall_back(self, app, bad):
        """A hand-typed URL must not break a page either."""
        with app.test_request_context('/', query_string={'theme': bad}):
            assert read_theme() == 'light'

    def test_normalizes_rather_than_passing_through(self, app):
        with app.test_request_context('/', query_string={'theme': 'bogus'}):
            assert read_theme() in _THEMES

    def test_vocabulary_matches_the_chart_layer(self, app):
        """A theme the transport accepts but the charts cannot draw would cache
        an exception path. The two lists are declared in different files; this
        is what keeps them one list. (Same guard as
        ``test_every_name_reaches_a_chart_profile``.)"""
        from webapp.dashboards.charts.theme import THEMES

        assert _THEMES == set(THEMES)

    def test_is_shaped_like_read_layout(self):
        """The two readers must stay recognisably the same function.

        Not style policing: if they diverge, it is a sign someone reasoned
        about one axis without the other — which is exactly how the layout
        cache-key bug nearly happened. Both must be lenient (a membership test
        against a frozenset, no raising) and both must read query string then
        cookie.
        """
        import inspect
        theme_src = inspect.getsource(read_theme)
        layout_src = inspect.getsource(read_layout)
        for src, args, cookie in ((theme_src, 'theme', 'THEME_COOKIE'),
                                  (layout_src, 'layout', 'LAYOUT_COOKIE')):
            assert f"request.args.get('{args}')" in src
            assert f'request.cookies.get({cookie})' in src
            assert 'else default' in src
            assert 'abort' not in src and 'raise' not in src


# --------------------------------------------------------------------------
# The page shells
# --------------------------------------------------------------------------

#: Every ``<html>`` tag the app owns. ``errors/429.html`` inherits from the
#: dashboard base and ``admin/master.html`` is Flask-Admin's own Bootstrap 3
#: shell (explicitly out of scope — see docs/plans/implemented/DARK_MODE.md).
OWNED_SHELLS = ('dashboards/base.html', 'auth/login.html')


@pytest.mark.parametrize('shell', OWNED_SHELLS)
def test_shell_renders_the_theme_onto_the_root_element(shell):
    """Server-rendered, not script-set. This is the whole design.

    Asserted on the template source rather than a response so it covers the
    login page, which is a separate shell with separate CSS and is otherwise
    easy to forget.
    """
    html = (TEMPLATES / shell).read_text()
    match = re.search(r'<html\b[^>]*>', html)
    assert match, f'{shell} has no <html> tag'
    assert 'data-bs-theme="{{ theme }}"' in match.group(0), (
        f'{shell} does not render the theme onto its root element: '
        f'{match.group(0)}')


@pytest.mark.parametrize('shell', OWNED_SHELLS)
def test_shell_loads_the_toggle_script(shell):
    """``login.html`` does not extend the dashboard base, so it needs its own
    <script> tag — a real omission that would leave the login page with a
    toggle that does nothing."""
    html = (TEMPLATES / shell).read_text()
    assert 'js/theme-toggle.js' in html


class TestThemeRoundTrip:
    """Cookie in, attribute out — over a real request."""

    def test_login_page_defaults_to_light(self, client):
        body = client.get('/auth/login').get_data(as_text=True)
        assert 'data-bs-theme="light"' in body

    def test_login_page_honours_the_cookie(self, client):
        client.set_cookie(THEME_COOKIE, 'dark')
        body = client.get('/auth/login').get_data(as_text=True)
        assert 'data-bs-theme="dark"' in body

    def test_dashboard_honours_the_cookie(self, auth_client):
        auth_client.set_cookie(THEME_COOKIE, 'dark')
        body = auth_client.get('/user/info').get_data(as_text=True)
        assert 'data-bs-theme="dark"' in body

    def test_a_junk_cookie_still_renders_a_page(self, client):
        """The failure mode this prevents is a 500 on every route for anyone
        holding a stale cookie after a vocabulary change."""
        client.set_cookie(THEME_COOKIE, 'chartreuse')
        response = client.get('/auth/login')
        assert response.status_code == 200
        assert 'data-bs-theme="light"' in response.get_data(as_text=True)


# --------------------------------------------------------------------------
# The cached-HTML hazard
# --------------------------------------------------------------------------

class TestCacheKeyPartitionsByTheme:
    """Five routes cache fully-rendered HTML under ``user_aware_cache_key``.

    Today their bytes are genuinely theme-independent — they are table/card
    fragments with no chart SVG and no ``data-bs-theme`` of their own, so
    theming reaches them by CSS inheritance from the page shell. Strictly the
    key does not need a theme component.

    It has one anyway, because that invariant is real today and completely
    invisible tomorrow: add one chart to the allocations fragment and one
    user's dark SVG is served to every light-mode user with the same facility
    scope. That presents as an intermittent *rendering* bug rather than a
    caching bug. Make the wrong thing inexpressible — the same argument
    ``charts/base.py:chart_view`` makes about its own aliasing trap.
    """

    @pytest.fixture(autouse=True)
    def _unscoped(self, monkeypatch):
        monkeypatch.setattr('webapp.utils.rbac.user_facility_scope',
                            lambda user, perm: None)

    def _key(self, app, **ctx):
        with app.test_request_context('/allocations/projects', **ctx):
            return user_aware_cache_key()

    def test_theme_is_in_the_key(self, app):
        light = self._key(app)
        dark = self._key(app, headers={'Cookie': f'{THEME_COOKIE}=dark'})
        assert light != dark, 'two themes share a cached-HTML slot'

    def test_same_theme_shares_a_slot(self, app):
        a = self._key(app, headers={'Cookie': f'{THEME_COOKIE}=dark'})
        b = self._key(app, headers={'Cookie': f'{THEME_COOKIE}=dark'})
        assert a == b

    def test_theme_and_layout_partition_independently(self, app):
        """Four distinct slots from two binary axes. A key that folded them
        together — or dropped one — would still pass the pairwise tests."""
        keys = set()
        for theme in ('light', 'dark'):
            for layout in ('desktop', 'mobile'):
                keys.add(self._key(app, headers={
                    'Cookie': f'{THEME_COOKIE}={theme}; {LAYOUT_COOKIE}={layout}'}))
        assert len(keys) == 4, f'expected 4 distinct slots, got {len(keys)}'

    def test_junk_theme_does_not_fragment_the_key_space(self, app):
        """``read_theme`` normalizes, so a hand-typed value cannot mint
        unbounded cache entries. The key is shared across workers and pods."""
        a = self._key(app, headers={'Cookie': f'{THEME_COOKIE}=chartreuse'})
        b = self._key(app, headers={'Cookie': f'{THEME_COOKIE}=magenta'})
        assert a == b == self._key(app)


# --------------------------------------------------------------------------
# The sender
# --------------------------------------------------------------------------

class TestThemeToggleJs:
    """Asserted against the source, like ``TestLayoutAxisJs`` — there is no JS
    test runner in this repo, and each of these is a real bug when absent."""

    @pytest.fixture
    def js(self):
        return JS.read_text()

    def test_cookie_name_matches_the_server(self, js):
        assert f"'{THEME_COOKIE}'" in js or f'"{THEME_COOKIE}"' in js

    def test_cookie_is_persistent(self, js):
        """Unlike ``sam_layout`` (a session cookie — the viewport is a property
        of this visit), a theme is a preference to remember. A session cookie
        here would silently reset everyone to light on every browser restart."""
        assert 'max-age=' in js
        assert re.search(r'60\s*\*\s*60\s*\*\s*24\s*\*\s*365', js), (
            'expected a one-year Max-Age expressed legibly')

    def test_reloads_after_flipping(self, js):
        """16 charts are server-rendered SVG with baked colors. Without the
        reload they stay stale — a light chart on a dark page, which is worse
        than the reload it avoids."""
        assert 'location.reload()' in js

    def test_writes_the_cookie_before_reloading(self, js):
        """Order is the whole behavior: reload first and the server has not
        been told yet, so the page comes back in the OLD theme and the toggle
        looks broken."""
        assert js.index('writeCookie(next)') < js.index('location.reload()')

    def test_sets_the_attribute_before_reloading(self, js):
        """So the outgoing page paints the new theme instead of flashing the
        old one on the way out."""
        assert (js.index("setAttribute('data-bs-theme', next)")
                < js.index('location.reload()'))

    def test_does_not_inject_an_htmx_parameter(self, js):
        """The deliberate divergence from ``layout-axis.js``. A theme is
        declared, not discovered, so the cookie and the browser can never
        disagree — an injected ``?theme=`` would split the chart and
        rendered-HTML cache key spaces on a value that cannot vary."""
        assert 'htmx:configRequest' not in js
        assert "'theme'" not in js.replace("var COOKIE = 'sam_theme'", '')

    def test_targets_an_attribute_not_an_id(self, js):
        """More than one toggle is on the page at once (desktop utility menu +
        mobile drawer). An id-based binding would wire up only the first."""
        assert 'data-theme-toggle' in js
        assert 'getElementById' not in js

    def test_keeps_every_copy_in_sync(self, js):
        """Flipping the desktop control must relabel the drawer control too —
        they are both in the DOM, and a stale 'Switch to dark mode' on one of
        them is a visible inconsistency."""
        assert 'querySelectorAll' in js and 'data-theme-toggle' in js


class TestReversedBrandMark:
    """The dark navbar and the reversed lockup are one decision.

    `--surface-navbar` goes dark ONLY because both shells swap in
    `logo-ncar-reversed.png` at the same time. Change one without the other and
    you get a navy "NCAR" wordmark on a navy band — legible in the mockup, not
    on the page.
    """

    ASSET = 'img/logo-ncar-reversed.png'

    def test_the_reversed_asset_exists(self):
        path = (Path(__file__).resolve().parents[2] / 'src' / 'webapp'
                / 'static' / self.ASSET)
        assert path.exists(), (
            f'{self.ASSET} is missing, but the dark navbar assumes it — the '
            f'brand lockup would render navy-on-navy')

    @pytest.mark.parametrize('shell', OWNED_SHELLS)
    def test_both_shells_swap_the_mark(self, shell):
        html = (TEMPLATES / shell).read_text()
        assert self.ASSET in html, (
            f'{shell} always renders the light lockup; on a dark navbar its '
            f'"NCAR" wordmark disappears')
        assert "theme == 'dark'" in html, (
            f'{shell} references the reversed mark but does not gate it on '
            f'the theme')

    def test_dark_navbar_is_not_left_light(self):
        """Guards the other direction: a `--surface-navbar` still pinned white
        in the dark block would put the *reversed* (white) wordmark on a white
        band — the same bug, mirrored."""
        css = (Path(__file__).resolve().parents[2] / 'src' / 'webapp'
               / 'static' / 'css' / 'variables.css').read_text()
        # Strip comments first: the selector is also *named* in the prose
        # explaining the specificity rule, and matching that would read the
        # light block's value instead.
        css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        dark = css[css.index(':root[data-bs-theme="dark"]'):]
        block = dark[:dark.index('}')]
        match = re.search(r'--surface-navbar:\s*([^;]+);', block)
        assert match, 'the dark block does not set --surface-navbar'
        value = match.group(1).strip().lower()
        assert value not in ('#fff', '#ffffff', 'white'), (
            f'--surface-navbar is {value} in the dark block, but the shells '
            f'serve the white reversed wordmark — it would be invisible')


def test_toggle_macro_is_used_by_every_shell():
    """One macro, three call sites. A shell that hand-rolled the button would
    drift from the JS contract (``data-theme-toggle``, ``data-theme-icon``)
    without failing anything else."""
    macro = TEMPLATES / 'dashboards' / 'fragments' / 'theme_toggle.html'
    assert macro.exists()
    assert 'data-theme-toggle' in macro.read_text()

    users = ('dashboards/base.html',
             'dashboards/fragments/mobile_nav.html',
             'auth/login.html')
    for name in users:
        assert 'theme_toggle' in (TEMPLATES / name).read_text(), (
            f'{name} does not use the shared theme_toggle macro')
