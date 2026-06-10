"""
Vendor-asset registry tests (PRODUCTION_IMPROVEMENTS item 3).

The registry (webapp/vendor_assets.py) is the single source of truth for
CDN assets; these tests pin its invariants and confirm the macros render
SRI attributes into the served pages. The *real* SRI verification is the
browser — a hash mismatch hard-fails the asset load — covered by the
webdev visual smoke test.
"""

import re

from webapp.vendor_assets import VENDOR_ASSETS

# Google Fonts CSS is generated per-User-Agent; SRI is impossible.
SRI_EXEMPT = {'poppins'}


class TestRegistryInvariants:

    def test_all_urls_https(self):
        for name, asset in VENDOR_ASSETS.items():
            assert asset['url'].startswith('https://'), name

    def test_all_urls_version_pinned(self):
        """Google Fonts URLs carry a family spec, not a package version."""
        for name, asset in VENDOR_ASSETS.items():
            if name in SRI_EXEMPT:
                continue
            assert re.search(r'\d+\.\d+\.\d+', asset['url']), \
                f"{name} URL is not version-pinned: {asset['url']}"

    def test_kind_is_css_or_js(self):
        for name, asset in VENDOR_ASSETS.items():
            assert asset['kind'] in ('css', 'js'), name

    def test_sri_present_except_exempt(self):
        for name, asset in VENDOR_ASSETS.items():
            if name in SRI_EXEMPT:
                assert asset['integrity'] is None
                assert asset['crossorigin'] is None
            else:
                assert asset['integrity'].startswith('sha384-'), name
                assert asset['crossorigin'] == 'anonymous', name

    def test_sha384_hash_shape(self):
        """sha384 digest base64-encodes to exactly 64 characters."""
        for name, asset in VENDOR_ASSETS.items():
            if name in SRI_EXEMPT:
                continue
            b64 = asset['integrity'].removeprefix('sha384-')
            assert re.fullmatch(r'[A-Za-z0-9+/]{63}[A-Za-z0-9+/=]', b64), name


class TestRenderedTemplates:

    def test_login_page_renders_sri(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        for name in ('bootstrap-css', 'fontawesome-css', 'jquery', 'bootstrap-js'):
            asset = VENDOR_ASSETS[name]
            assert asset['url'] in html, name
            assert asset['integrity'] in html, name

    def test_google_fonts_link_has_no_integrity(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        fonts_tag = next(line for line in html.splitlines()
                         if 'fonts.googleapis.com/css2' in line)
        assert 'integrity=' not in fonts_tag

    def test_dashboard_base_renders_htmx_with_sri(self, auth_client):
        html = auth_client.get('/user/').get_data(as_text=True)
        htmx = VENDOR_ASSETS['htmx']
        assert htmx['url'] in html
        assert htmx['integrity'] in html
