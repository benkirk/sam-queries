"""How ``layout=mobile`` gets from the browser to the chart renderer.

Two channels, because charts reach the browser two different ways and neither
one covers both:

- **query string**, injected into every htmx request by
  ``static/js/layout-axis.js``. Covers the 9 fragment call sites, and covers
  them on the very first paint because ``hx-trigger="load"`` fires after the
  listener registers.
- **cookie**, written by the same file from ``matchMedia``. Covers the 9
  call sites that render inside a full-page GET — the three status history
  pages and the four pies on ``/allocations/projects`` — where no htmx
  request exists to inject into.

The cookie cannot cover the first page of a session (it is set at end of body,
after the server already chose), and the query string cannot reach a full-page
render. Together they leave only that one first page, on which charts are
merely small rather than broken.
"""

import re
from pathlib import Path

import pytest

from webapp.extensions import user_aware_cache_key
from webapp.utils.htmx import LAYOUT_COOKIE, read_layout

pytestmark = pytest.mark.unit

JS = (Path(__file__).resolve().parents[2]
      / 'src' / 'webapp' / 'static' / 'js' / 'layout-axis.js')


# --------------------------------------------------------------------------
# read_layout
# --------------------------------------------------------------------------

class TestReadLayout:

    def test_defaults_to_desktop(self, app):
        with app.test_request_context('/'):
            assert read_layout() == 'desktop'

    @pytest.mark.parametrize('value', ['mobile', 'MOBILE', ' mobile ',
                                       'tablet', 'TABLET', ' tablet '])
    def test_query_string_selects_a_declared_layout(self, app, value):
        with app.test_request_context('/', query_string={'layout': value}):
            assert read_layout() == value.strip().lower()

    @pytest.mark.parametrize('value', ['mobile', 'tablet'])
    def test_cookie_selects_a_declared_layout(self, app, value):
        with app.test_request_context(
                '/', headers={'Cookie': f'{LAYOUT_COOKIE}={value}'}):
            assert read_layout() == value

    def test_query_string_outranks_the_cookie(self, app):
        """The fragment reflects the viewport *now*; the cookie reflects
        whatever it was when the page was served. A rotated phone should not
        be pinned to a stale cookie for the rest of the session."""
        with app.test_request_context(
                '/', query_string={'layout': 'desktop'},
                headers={'Cookie': f'{LAYOUT_COOKIE}=mobile'}):
            assert read_layout() == 'desktop'

    @pytest.mark.parametrize('bad', ['sideways', '', 'phone', '1', 'mobile;'])
    def test_unknown_values_fall_back(self, app, bad):
        """Lenient like ``jobs/routes.py:_parse_period`` — an unknown value
        means "no override", never a 400. These are htmx fragments; a stale
        replay or a hand-typed URL must not break a card."""
        with app.test_request_context('/', query_string={'layout': bad}):
            assert read_layout() == 'desktop'

    def test_normalizes_rather_than_passing_through(self, app):
        """The chart layer is lenient too, so passing an unknown value along
        would render correctly — but it would reach the *cache key*, and that
        key is shared across workers and pods. The declared spellings, not
        arbitrarily many."""
        with app.test_request_context('/', query_string={'layout': 'bogus'}):
            assert read_layout() in ('desktop', 'tablet', 'mobile')

    def test_every_name_reaches_a_chart_profile(self, app):
        """A vocabulary the transport accepts but the charts cannot draw would
        cache an exception path. The two lists are declared in different files;
        this is what keeps them one list."""
        from webapp.dashboards.charts import pie
        from webapp.utils.htmx import _LAYOUTS

        assert _LAYOUTS == set(pie.PieChart.LAYOUTS)


# --------------------------------------------------------------------------
# The cached-HTML hazard
# --------------------------------------------------------------------------

class TestCacheKeyPartitionsByLayout:
    """``/allocations/projects`` renders four pies inline and is
    ``@cache.cached``. Its layout arrives on the cookie, which is *not* in the
    query string — so without this, the first visitor to warm the page would
    decide whether every later visitor got phone-sized or desktop-sized pies.
    """

    @pytest.fixture(autouse=True)
    def _unscoped(self, monkeypatch):
        """Neutralize the key's facility-scope half.

        It reads `current_user.roles`, which needs a real login; these tests
        are about the layout half, and pinning the scope to "unscoped" keeps
        the two independent — a scope change must not be able to make a
        layout assertion pass or fail.
        """
        monkeypatch.setattr('webapp.utils.rbac.user_facility_scope',
                            lambda user, perm: None)

    def _key(self, app, **ctx):
        with app.test_request_context('/allocations/projects', **ctx):
            return user_aware_cache_key()

    def test_layout_is_in_the_key(self, app):
        keys = {self._key(app)}
        for name in ('mobile', 'tablet'):
            keys.add(self._key(app, headers={'Cookie': f'{LAYOUT_COOKIE}={name}'}))
        assert len(keys) == 3, 'two layouts share a cached-HTML slot'

    def test_same_layout_shares_a_slot(self, app):
        a = self._key(app, headers={'Cookie': f'{LAYOUT_COOKIE}=mobile'})
        b = self._key(app, headers={'Cookie': f'{LAYOUT_COOKIE}=mobile'})
        assert a == b

    def test_query_string_still_partitions(self, app):
        """Pre-existing behaviour, kept."""
        assert (self._key(app, query_string={'resources': 'Derecho'})
                != self._key(app))


# --------------------------------------------------------------------------
# The sender
# --------------------------------------------------------------------------

class TestLayoutAxisJs:
    """Asserted against the source because there is no JS test runner in this
    repo. Crude, but these four properties are each a real bug when absent,
    and three of them are invisible until someone opens a phone.
    """

    @pytest.fixture
    def js(self):
        return JS.read_text()

    #: Bootstrap's `max-width` breakpoints (each 0.02px under the `min-width`
    #: one). A layout boundary that is not one of these is a number somebody
    #: invented, and it will drift away from the CSS that has to agree with it.
    BOOTSTRAP_MAX_WIDTHS = {'575.98', '767.98', '991.98', '1199.98', '1399.98'}

    def test_uses_the_app_wide_breakpoint(self, js):
        """Bootstrap's `md`, matching ``dashboard-init.js``'s
        ``collapseFilterPanels``. Two definitions of "phone" would put the
        filter panels and the charts on different sides of the same window.

        This used to assert the two files' query *sets* were equal, which was
        right while "phone or not" was the only question. The tablet band adds
        a second boundary that ``dashboard-init.js`` has no opinion on, so the
        claim narrows to: every breakpoint that file uses is one this file also
        uses, and anything extra here is a real Bootstrap breakpoint.
        """
        assert '(max-width: 767.98px)' in js

        other = (JS.parent / 'dashboard-init.js').read_text()
        # Both files may hold the query in a constant, so match the literal
        # rather than the matchMedia call site.
        query = r"\(max-width: ([\d.]+)px\)"
        mine = set(re.findall(query, js))
        theirs = set(re.findall(query, other))
        assert theirs and theirs <= mine, (
            f'breakpoint drift: dashboard-init uses {theirs - mine} which '
            f'layout-axis does not')
        assert mine <= self.BOOTSTRAP_MAX_WIDTHS, (
            f'layout-axis invented a breakpoint: '
            f'{sorted(mine - self.BOOTSTRAP_MAX_WIDTHS)}')

    def test_selects_the_narrowest_matching_band(self, js):
        """Below 768 both queries match, so the order of the tests is the
        whole behaviour: mobile must be asked first or a phone gets the tablet
        figure. Asserted positionally because there is no JS test runner."""
        assert js.index('MOBILE_QUERY).matches') < js.index('TABLET_QUERY).matches')

    def test_cookie_name_matches_the_server(self, js):
        assert f"'{LAYOUT_COOKIE}'" in js or f'"{LAYOUT_COOKIE}"' in js

    def test_injects_unconditionally(self, js):
        """Unlike ``nav-view-persistence.js``'s opt-in injector, this one has
        no ``elt.matches`` gate: a chart fragment that forgot a marker
        attribute would silently render desktop."""
        listener = js[js.index("addEventListener('htmx:configRequest'"):]
        assert 'elt.matches' not in listener

    def test_dedupes_the_param_from_the_path(self, js):
        """htmx appends ``parameters`` to ``path`` for GETs, so a ``layout``
        already in an hx-get URL yields a duplicate query key — and
        ``request.args.get`` returns the FIRST, silently defeating the
        override. Same hazard and same fix as ``injectSaved``."""
        assert 'URLSearchParams' in js and 'delete(PARAM)' in js

    def test_has_no_resize_listener(self, js):
        """Deliberate: a drag-resize across the breakpoint would fire a burst
        of chart renders, each a matplotlib figure. Documented in the module
        header, asserted here so it stays a decision rather than an
        oversight."""
        assert "addEventListener('resize'" not in js
        assert '.addListener(' not in js
        assert "addEventListener('change'" not in js

    def test_is_loaded_by_the_dashboard_base_template(self):
        base = (JS.parent.parent.parent / 'templates' / 'dashboards'
                / 'base.html').read_text()
        assert 'js/layout-axis.js' in base
