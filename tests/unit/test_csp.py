"""
CSP builder tests (webapp/utils/csp.py).

The policy derives from the vendor-asset registry; with everything
vendored it collapses to 'self'. The fake-external-entry tests pin the
anti-drift guarantee: a future CDN asset's origin flows into the right
directives with no header-side edits.
"""

from webapp.utils.csp import build_csp_directives, build_csp_policy, render_csp
from webapp.vendor_assets import VENDOR_ASSETS


def _build(assets=VENDOR_ASSETS, **config):
    return build_csp_directives(assets, config)


class TestRealRegistry:
    """Directives derived from the actual (fully-vendored) registry."""

    def test_script_src_is_self_only(self):
        assert _build()['script-src'] == ["'self'"]

    def test_style_src_self_plus_unsafe_inline_only(self):
        assert _build()['style-src'] == ["'self'", "'unsafe-inline'"]

    def test_no_nonce_anywhere(self):
        assert 'nonce' not in build_csp_policy(VENDOR_ASSETS, {})

    def test_font_and_connect_src_are_self(self):
        d = _build()
        assert d['font-src'] == ["'self'"]
        assert d['connect-src'] == ["'self'"]

    def test_img_src_allows_data_uris(self):
        assert _build()['img-src'] == ["'self'", 'data:']

    def test_hardening_directives(self):
        d = _build()
        assert d['object-src'] == ["'none'"]
        assert d['base-uri'] == ["'self'"]
        assert d['form-action'] == ["'self'"]
        assert d['frame-ancestors'] == ["'self'"]

    def test_rendered_policy_shape(self):
        policy = build_csp_policy(VENDOR_ASSETS, {})
        assert policy.startswith("default-src 'self'; ")
        assert "script-src 'self'; " in policy
        assert 'http' not in policy   # zero external origins


class TestCalendarEmbed:

    def test_frame_src_default(self):
        assert _build()['frame-src'] == ["'self'"]

    def test_frame_src_picks_up_calendar_origin(self):
        d = _build(GOOGLE_CALENDAR_EMBED_URL=(
            'https://calendar.google.com/calendar/embed?src=abc%40group'))
        assert d['frame-src'] == ["'self'", 'https://calendar.google.com']


class TestFutureExternalAssets:
    """A registry entry with url= / csp_extra= flows into the policy."""

    def test_external_js_origin_lands_in_script_src(self):
        assets = dict(VENDOR_ASSETS)
        assets['somelib'] = {'kind': 'js',
                             'url': 'https://cdn.example.net/somelib/1.2.3/somelib.min.js'}
        d = _build(assets)
        assert d['script-src'] == ["'self'", 'https://cdn.example.net']
        assert 'https://cdn.example.net' not in d['style-src']

    def test_external_css_origin_lands_in_style_and_font_src(self):
        assets = dict(VENDOR_ASSETS)
        assets['somecss'] = {'kind': 'css',
                             'url': 'https://cdn.example.net/somecss/1.2.3/somecss.min.css'}
        d = _build(assets)
        assert 'https://cdn.example.net' in d['style-src']
        assert 'https://cdn.example.net' in d['font-src']
        assert 'https://cdn.example.net' not in d['script-src']

    def test_csp_extra_extends_arbitrary_directive(self):
        assets = dict(VENDOR_ASSETS)
        assets['fonty'] = {'kind': 'css',
                           'url': 'https://fonts.example.com/fonty.css',
                           'csp_extra': {'font-src': 'https://static.example.com'}}
        d = _build(assets)
        assert 'https://static.example.com' in d['font-src']

    def test_duplicate_origins_collapse(self):
        assets = dict(VENDOR_ASSETS)
        assets['a'] = {'kind': 'js', 'url': 'https://cdn.example.net/a/1.0.0/a.js'}
        assets['b'] = {'kind': 'js', 'url': 'https://cdn.example.net/b/2.0.0/b.js'}
        assert _build(assets)['script-src'] == ["'self'", 'https://cdn.example.net']


def test_render_csp_joins_directives():
    assert render_csp({'default-src': ["'self'"],
                       'img-src': ["'self'", 'data:']}) == \
        "default-src 'self'; img-src 'self' data:"
