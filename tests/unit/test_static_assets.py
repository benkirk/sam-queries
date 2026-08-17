"""Static-asset cache headers and cache-busting URLs (webapp.utils.static_assets).

Flask's default is ``Cache-Control: no-cache`` on every static file, which made
87.5% of one production pod's requests be /static revalidations answering 304
(measured 2026-08-17; see the module docstring). The fix is two halves that are
only correct together — ``url_for`` emits ``?v=<content hash>``, and a request
carrying that parameter is declared immutable for a year — so both halves are
asserted here, plus the invariant the design leans on: that every template
reference actually goes through ``url_for``.
"""

import re
from pathlib import Path

from flask import url_for

from webapp.utils.static_assets import (
    IMMUTABLE_MAX_AGE,
    UNVERSIONED_MAX_AGE,
    asset_version,
)

# An app-owned asset and a vendored one: both must behave identically, because
# the rule keys on `?v=` and not on the path.
APP_ASSET = 'js/form-helpers.js'
VENDOR_ASSET = 'vendor/htmx/htmx-2.0.4.min.js'

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'


class TestTheVersionTag:
    """`?v=` is a content hash, and url_for adds it."""

    def test_url_for_static_carries_a_version(self, app):
        with app.test_request_context():
            url = url_for('static', filename=APP_ASSET)
        assert re.search(r'\?v=[0-9a-f]{16}$', url), url

    def test_the_version_is_stable_across_calls(self, app):
        """It must be, or every render would bust the cache it just created."""
        with app.test_request_context():
            first = url_for('static', filename=APP_ASSET)
            second = url_for('static', filename=APP_ASSET)
        assert first == second

    def test_different_files_get_different_versions(self, app):
        with app.test_request_context():
            a = url_for('static', filename=APP_ASSET)
            b = url_for('static', filename=VENDOR_ASSET)
        assert a.split('?v=')[1] != b.split('?v=')[1]

    def test_the_version_tracks_content_not_mtime(self, tmp_path):
        """Content, not mtime: a container rebuild restamps every mtime, so an
        mtime tag would invalidate every asset on every deploy — discarding
        exactly the cache this module exists to create."""
        asset = tmp_path / 'a.js'
        asset.write_text('one')
        before = asset_version(tmp_path, 'a.js')

        asset.write_text('two')                       # same path, new content
        assert asset_version(tmp_path, 'a.js') != before

        asset.write_text('one')                       # back to the original
        assert asset_version(tmp_path, 'a.js') == before

    def test_touching_a_file_does_not_change_its_version(self, tmp_path):
        """The other half of the same property, stated positively."""
        asset = tmp_path / 'a.js'
        asset.write_text('one')
        before = asset_version(tmp_path, 'a.js')
        asset.touch()
        assert asset_version(tmp_path, 'a.js') == before

    def test_a_missing_asset_yields_no_version_rather_than_raising(self, app):
        """A template naming a missing asset is already a 404 the browser
        reports; a 500 on every page that mentions it would be worse."""
        assert asset_version(app.static_folder, 'js/does-not-exist.js') is None
        with app.test_request_context():
            url = url_for('static', filename='js/does-not-exist.js')
        assert '?v=' not in url

    def test_a_traversing_filename_is_refused(self, app):
        """safe_join guards the one place a stray '..' would read outside the
        static root."""
        assert asset_version(app.static_folder, '../config.py') is None


class TestTheCacheHeader:
    """ONE rule: `?v=` present or not."""

    def test_a_versioned_request_is_immutable_for_a_year(self, client, app):
        with app.test_request_context():
            url = url_for('static', filename=APP_ASSET)
        resp = client.get(url)
        assert resp.status_code == 200
        cc = resp.headers['Cache-Control']
        assert f'max-age={IMMUTABLE_MAX_AGE}' in cc
        assert 'immutable' in cc
        assert 'public' in cc

    def test_a_vendored_asset_is_treated_the_same_way(self, client, app):
        """The rule keys on `?v=`, not on the path — so there is no vendor
        special case to drift out of sync with the tree layout."""
        with app.test_request_context():
            url = url_for('static', filename=VENDOR_ASSET)
        cc = client.get(url).headers['Cache-Control']
        assert f'max-age={IMMUTABLE_MAX_AGE}' in cc and 'immutable' in cc

    def test_an_unversioned_request_gets_the_short_ttl(self, client):
        """The branch that covers assets no url_for can reach: the relative
        url() targets inside a stylesheet (FontAwesome's ../webfonts/*, and the
        one ../img/ reference in the app's own CSS). Bounded, not unbounded, so
        replacing such a file in place self-corrects."""
        resp = client.get(f'/static/{APP_ASSET}')
        assert resp.status_code == 200
        cc = resp.headers['Cache-Control']
        assert f'max-age={UNVERSIONED_MAX_AGE}' in cc
        assert 'immutable' not in cc

    def test_the_conservative_branch_is_the_default(self, client):
        """Stated as its own assertion because it is the design's safety
        property: getting the rule wrong must cost efficiency, never
        correctness. An asset referenced some novel way lands here."""
        cc = client.get(f'/static/{VENDOR_ASSET}').headers['Cache-Control']
        assert 'immutable' not in cc
        assert f'max-age={UNVERSIONED_MAX_AGE}' in cc

    def test_no_cache_is_gone(self, client, app):
        """Flask's default, and the entire cost being removed."""
        with app.test_request_context():
            url = url_for('static', filename=APP_ASSET)
        for target in (url, f'/static/{APP_ASSET}'):
            assert 'no-cache' not in client.get(target).headers['Cache-Control']

    def test_expires_does_not_contradict_cache_control(self, client, app):
        """send_file sets Expires alongside its own Cache-Control; a leftover
        value would be obeyed by an HTTP/1.0 cache in preference to ours."""
        with app.test_request_context():
            url = url_for('static', filename=APP_ASSET)
        assert 'Expires' not in client.get(url).headers

    def test_a_conditional_request_still_carries_the_header(self, client, app):
        """304s are the traffic being eliminated, but any that remain must
        still update the browser's freshness — otherwise a client that
        revalidates once keeps revalidating for ever."""
        with app.test_request_context():
            url = url_for('static', filename=APP_ASSET)
        etag = client.get(url).headers['ETag']
        resp = client.get(url, headers={'If-None-Match': etag})
        assert resp.status_code == 304
        assert 'immutable' in resp.headers['Cache-Control']

    def test_non_static_responses_are_untouched(self, client):
        """The hook is endpoint-scoped: an HTML page must not inherit a
        one-year TTL."""
        resp = client.get('/auth/login')
        assert f'max-age={IMMUTABLE_MAX_AGE}' not in resp.headers.get('Cache-Control', '')


class TestTheInvariantTheDesignRelieson:
    """The single rule is only sufficient because every template reference goes
    through url_for. These are the gates that keep that true."""

    def test_a_real_page_emits_only_versioned_static_urls(self, client):
        """The end-to-end property, on actual rendered HTML rather than by
        inspecting the mechanism: every asset the browser is told to fetch
        carries a `?v=`, so every one of them lands on the immutable branch.

        This is the assertion that would have caught the original problem, and
        the one that fails if a future template reaches for a raw path."""
        html = client.get('/auth/login').get_data(as_text=True)
        refs = sorted(set(re.findall(r'/static/[^"\')\s]+', html)))
        assert refs, 'no static references found — did the login page change?'
        unversioned = [r for r in refs if '?v=' not in r]
        assert not unversioned, unversioned

    def test_no_template_hardcodes_a_static_path(self):
        """A hardcoded /static/... path silently takes the short TTL instead of
        the immutable one. Safe, but it forfeits the win for that asset — so
        catch it here rather than in a traffic graph months later."""
        offenders = []
        for path in TEMPLATE_ROOT.rglob('*.html'):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r'''(href|src)=["']/static/''', line):
                    offenders.append(f'{path.name}:{n}')
        assert not offenders, (
            'use url_for("static", filename=...) so the asset gets a ?v= tag: '
            + ', '.join(offenders))

    def test_no_template_appends_a_query_after_url_for_static(self):
        """This one is a correctness gate, not an efficiency one: url_for now
        returns a URL that already has a query string, so `{{ url_for(...) }}?x`
        produces `?v=abc?x` and the asset 404s or the parameter is lost."""
        offenders = []
        pattern = re.compile(r'''url_for\(\s*['"]static['"][^)]*\)\s*\}\}\s*[?#]''')
        for path in TEMPLATE_ROOT.rglob('*.html'):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f'{path.name}:{n}')
        assert not offenders, (
            'url_for("static") already returns a query string; pass extra '
            'parameters as url_for kwargs instead: ' + ', '.join(offenders))
