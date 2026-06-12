"""Content-Security-Policy generation.

The policy is GENERATED from webapp.vendor_assets.VENDOR_ASSETS so the
header and the templates cannot drift: with every asset vendored locally
(static/vendor/) the policy collapses to 'self', and if a genuinely
external asset is ever added to the registry (url= entry, optionally
csp_extra=) its origin flows into the right directives automatically.

Design constraints (see docs/plans/DEFERRED-CSP-discussion.md):

- Nonce-free by design. Four routes cache fully-rendered HTML in Redis
  per-user (orgs/institutions cards, allocations dashboard + fragment);
  a per-request nonce would go stale on every cache hit. Instead, the
  templates carry zero inline executable scripts — behavior lives in
  static JS, dynamic data rides data-* attributes or non-executable
  <script type="application/json"> blocks. tests/unit/test_template_csp_lint.py
  enforces this at CI time.
- style-src keeps 'unsafe-inline': ~245 inline style= attributes
  (widths, tree-depth padding, ellipsis) are an accepted pragmatic
  tradeoff — inline styles cannot execute script, and injecting a
  <style> block already requires the HTML-injection failure that Jinja
  autoescaping prevents.
- frame-src widens to the GOOGLE_CALENDAR_EMBED_URL origin when that
  iframe is configured (status dashboard reservations tab).
"""

from urllib.parse import urlsplit

SELF = "'self'"

#: registry `kind` → directives an external asset's origin must appear in.
#: css assets feed font-src too: stylesheets pull webfonts from their own
#: origin by relative path (Font Awesome ../webfonts/).
_KIND_DIRECTIVES = {
    'js':  ('script-src',),
    'css': ('style-src', 'font-src'),
}


def _origin(url):
    """scheme://host of a URL: https://cdn.example.net/pkg/x.js → https://cdn.example.net"""
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


def _add(directives, name, source):
    values = directives.setdefault(name, [SELF])
    if source not in values:
        values.append(source)


def build_csp_directives(vendor_assets, config):
    """Derive the directive map from the vendor registry + app config."""
    directives = {
        'default-src':     [SELF],
        # Nonce-free: no 'unsafe-inline', no nonces (cached-HTML constraint).
        'script-src':      [SELF],
        # 'unsafe-inline' for STYLE only — see module docstring.
        'style-src':       [SELF, "'unsafe-inline'"],
        'font-src':        [SELF],
        # data: for Bootstrap's and dashboard.css's inline SVG data URIs.
        'img-src':         [SELF, 'data:'],
        'connect-src':     [SELF],
        'frame-src':       [SELF],
        # Replaces X-Frame-Options: SAMEORIGIN once the policy enforces
        # (browsers ignore frame-ancestors in Report-Only mode).
        'frame-ancestors': [SELF],
        'object-src':      ["'none'"],
        'base-uri':        [SELF],
        # OIDC login is a pure 302 redirect; no IdP form posts.
        'form-action':     [SELF],
    }

    for asset in vendor_assets.values():
        url = asset.get('url')
        if url:
            for directive in _KIND_DIRECTIVES[asset['kind']]:
                _add(directives, directive, _origin(url))
        for directive, source in (asset.get('csp_extra') or {}).items():
            _add(directives, directive, source)

    calendar_url = config.get('GOOGLE_CALENDAR_EMBED_URL', '')
    if calendar_url:
        _add(directives, 'frame-src', _origin(calendar_url))

    return directives


def render_csp(directives):
    return '; '.join(
        '{} {}'.format(name, ' '.join(values))
        for name, values in directives.items()
    )


def build_csp_policy(vendor_assets, config):
    """The one-call form used by init_security_headers()."""
    return render_csp(build_csp_directives(vendor_assets, config))
