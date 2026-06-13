"""
Vendor-asset registry tests (PRODUCTION_IMPROVEMENTS item 3 + CSP work).

The registry (webapp/vendor_assets.py) is the single source of truth for
third-party assets, all vendored under static/vendor/ and committed to
the repo. With no CDN in the loop, browser-side SRI no longer applies —
instead these tests re-hash the committed files against the registry's
pinned sha384 values, so tampering or an accidental edit fails CI.
"""

import base64
import hashlib
import re
from pathlib import Path

from webapp.vendor_assets import VENDOR_ASSETS

STATIC_DIR = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'static'


def _sha384(path: Path) -> str:
    digest = hashlib.sha384(path.read_bytes()).digest()
    return 'sha384-' + base64.b64encode(digest).decode()


class TestRegistryInvariants:

    def test_all_assets_are_local(self):
        """No CDN entries remain; a future external asset would need
        url/integrity/crossorigin and a CSP review (see module docstring)."""
        for name, asset in VENDOR_ASSETS.items():
            assert 'path' in asset, name
            assert 'url' not in asset, name

    def test_paths_exist_under_static(self):
        for name, asset in VENDOR_ASSETS.items():
            assert (STATIC_DIR / asset['path']).is_file(), \
                f"{name}: static/{asset['path']} missing"

    def test_paths_are_version_pinned(self):
        """Poppins is pinned by Google Fonts API version in the css comment;
        its directory carries no semver."""
        for name, asset in VENDOR_ASSETS.items():
            if name == 'poppins':
                continue
            assert re.search(r'\d+\.\d+\.\d+', asset['path']), \
                f"{name} path is not version-pinned: {asset['path']}"

    def test_kind_is_css_or_js(self):
        for name, asset in VENDOR_ASSETS.items():
            assert asset['kind'] in ('css', 'js'), name

    def test_committed_files_match_pinned_hashes(self):
        """The tamper-evidence check: every entry-point file must hash to
        its registry sha384 (original publisher SRI for the formerly-CDN
        assets). An asset upgrade updates path+sha384 together."""
        for name, asset in VENDOR_ASSETS.items():
            assert _sha384(STATIC_DIR / asset['path']) == asset['sha384'], \
                f"{name}: static/{asset['path']} does not match pinned sha384"

    def test_fontawesome_webfonts_present(self):
        """all.min.css references ../webfonts/* by relative path; the css
        hash can't cover them, so assert the directory rode along."""
        css = (STATIC_DIR / VENDOR_ASSETS['fontawesome-css']['path']).read_text()
        webfonts_dir = (STATIC_DIR / VENDOR_ASSETS['fontawesome-css']['path']).parent.parent / 'webfonts'
        referenced = set(re.findall(r'webfonts/([a-z0-9.-]+\.(?:woff2|ttf))', css))
        assert referenced, 'expected webfont references in all.min.css'
        for fname in referenced:
            assert (webfonts_dir / fname).is_file(), f'missing webfont {fname}'

    def test_poppins_woff2_present(self):
        css_path = STATIC_DIR / VENDOR_ASSETS['poppins']['path']
        referenced = set(re.findall(r'url\(([a-z0-9.-]+\.woff2)\)', css_path.read_text()))
        assert referenced, 'expected woff2 references in poppins.css'
        for fname in referenced:
            assert (css_path.parent / fname).is_file(), f'missing woff2 {fname}'


class TestRenderedTemplates:

    def test_login_page_serves_local_assets(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        for name in ('poppins', 'bootstrap-css', 'fontawesome-css',
                     'jquery', 'bootstrap-js'):
            assert f"/static/{VENDOR_ASSETS[name]['path']}" in html, name

    def test_login_page_has_no_cdn_references(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        for origin in ('googleapis.com', 'gstatic.com', 'jsdelivr.net',
                       'cdnjs.cloudflare.com', 'code.jquery.com', 'unpkg.com'):
            assert origin not in html, origin

    def test_dashboard_base_serves_local_htmx(self, auth_client):
        html = auth_client.get('/user/').get_data(as_text=True)
        assert f"/static/{VENDOR_ASSETS['htmx']['path']}" in html
        assert 'unpkg.com' not in html
