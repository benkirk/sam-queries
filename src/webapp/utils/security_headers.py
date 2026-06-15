"""Baseline security response headers (PRODUCTION_IMPROVEMENTS item 3) + CSP.

The Content-Security-Policy is generated from webapp.vendor_assets via
webapp.utils.csp so the header and the templates can't drift; CSP_MODE
('enforce' | 'report-only' | 'off', see config.py) selects the header
name or disables it entirely.

Notes:
- HSTS is gated on SESSION_COOKIE_SECURE — the existing "this deployment is
  HTTPS-only" bit (True only in ProductionConfig). ProxyFix is wired in
  run.py, so the scheme seen here is trustworthy behind the ingress.
- Referrer-Policy must stay at strict-origin-when-cross-origin (not
  stricter): Flask-WTF's CSRF referrer check on HTTPS POSTs requires
  same-origin requests to carry a referrer.
- Cross-Origin-Resource-Policy and Permissions-Policy ride every response
  unconditionally (scheme-independent, safe in dev + prod). They closed the
  two residual Low findings from the 2026-06 ZAP re-scan; see
  docs/nrit-review-2026-05/09_zap_rescan-2026-06.md.
- X-Frame-Options is superseded by the policy's frame-ancestors 'self'
  once CSP enforces; in report-only/off modes it must survive, because
  browsers ignore frame-ancestors in a Report-Only policy.
- Flask-Admin (/database, dev-only: FLASK_ADMIN_ENABLED defaults OFF in
  ProductionConfig) ships bundled templates full of inline JS we don't
  control; CSP is skipped for that path prefix rather than relaxed —
  a permanently-violating dev-only surface would only generate noise.
- setdefault() everywhere so an individual route can deliberately override.
"""

from flask import request

from webapp.utils.csp import build_csp_policy

CSP_MODES = ('enforce', 'report-only', 'off')


def init_security_headers(app):
    hsts = app.config.get('SESSION_COOKIE_SECURE', False)

    mode = app.config.get('CSP_MODE', 'off')
    if mode not in CSP_MODES:
        raise ValueError(f"CSP_MODE={mode!r}; expected one of {CSP_MODES}")
    csp_header = csp_policy = None
    if mode != 'off':
        from webapp.vendor_assets import VENDOR_ASSETS
        # Registry and config are static per-process: build once at init.
        csp_policy = build_csp_policy(VENDOR_ASSETS, app.config)
        csp_header = ('Content-Security-Policy' if mode == 'enforce'
                      else 'Content-Security-Policy-Report-Only')

    @app.after_request
    def _set_security_headers(response):
        h = response.headers
        h.setdefault('X-Content-Type-Options', 'nosniff')
        h.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        # Defense-in-depth, scheme-independent (safe in dev + prod): isolate our
        # resources from cross-origin embedders, and disable powerful browser
        # features this dashboard never uses.
        h.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
        h.setdefault('Permissions-Policy',
                     'geolocation=(), camera=(), microphone=(), payment=(), usb=()')
        if mode != 'enforce':
            h.setdefault('X-Frame-Options', 'SAMEORIGIN')
        if hsts:
            h.setdefault('Strict-Transport-Security',
                         'max-age=31536000; includeSubDomains')
        if csp_header and not request.path.startswith('/database'):
            h.setdefault(csp_header, csp_policy)
        return response
