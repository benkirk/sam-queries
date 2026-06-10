"""Baseline security response headers (PRODUCTION_IMPROVEMENTS item 3).

CSP is deliberately deferred (inline-script audit pending) and, when it
lands, should be generated from webapp.vendor_assets so the header and the
templates can't drift.

Notes:
- HSTS is gated on SESSION_COOKIE_SECURE — the existing "this deployment is
  HTTPS-only" bit (True only in ProductionConfig). ProxyFix is wired in
  run.py, so the scheme seen here is trustworthy behind the ingress.
- Referrer-Policy must stay at strict-origin-when-cross-origin (not
  stricter): Flask-WTF's CSRF referrer check on HTTPS POSTs requires
  same-origin requests to carry a referrer.
- setdefault() everywhere so an individual route can deliberately override.
"""


def init_security_headers(app):
    hsts = app.config.get('SESSION_COOKIE_SECURE', False)

    @app.after_request
    def _set_security_headers(response):
        h = response.headers
        h.setdefault('X-Content-Type-Options', 'nosniff')
        h.setdefault('X-Frame-Options', 'SAMEORIGIN')
        h.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if hsts:
            h.setdefault('Strict-Transport-Security',
                         'max-age=31536000; includeSubDomains')
        return response
