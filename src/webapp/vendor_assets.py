"""Single source of truth for third-party (vendored) assets.

Every third-party asset the webapp loads is vendored into ``static/vendor/``
and committed, so the CSP (``webapp/utils/csp.py``) collapses to ``'self'``.
Allowlisting a mega-CDN would let an injected ``<script src>`` sidestep CSP
entirely, and cache partitioning removed the shared-CDN benefit years ago.

Each entry pins the sha384 of its entry-point file;
``tests/unit/test_vendor_assets.py`` re-hashes the committed files, so tampering
or an accidental edit fails CI. Compute with::

    openssl dgst -sha384 -binary <file> | openssl base64 -A

Upgrading: download, verify against the publisher's SRI where available, drop
it in ``static/vendor/``, update ``path`` + ``sha384`` together. Subresources
pulled in by relative path (Font Awesome ``../webfonts/``, Poppins woff2) ride
along in the same directory, integrity-protected by git itself.

An asset that must stay genuinely external gets ``url`` + ``integrity`` +
``crossorigin`` instead of ``path``, plus a ``csp_extra`` dict if it fetches
further resources at runtime. The CSP builder derives the policy from this
registry, so external origins flow into the header automatically.

Templates render these via the ``vendor_css()``/``vendor_js()`` macros; a
context processor in ``run.py`` exposes ``vendor_assets`` to every template.
"""

VENDOR_ASSETS = {
    # ---- CSS ----
    'poppins': {
        # Self-hosted Google Fonts Poppins v24 (weights 300-700) + local
        # @font-face sheet; sha384 covers our generated CSS entry point.
        'kind': 'css',
        'path': 'vendor/poppins/poppins.css',
        'sha384': 'sha384-CLbfPou1F0GY3Ht02socrZyF9lNSzFvzaq+d6KfPoZXaBxZ+vQCr7pa06VoJ3bEf',
    },
    'bootstrap-css': {
        'kind': 'css',
        'path': 'vendor/bootstrap-5.3.3/bootstrap.min.css',
        'sha384': 'sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH',
    },
    'fontawesome-css': {
        'kind': 'css',
        'path': 'vendor/fontawesome-6.5.2/css/all.min.css',
        'sha384': 'sha384-PPIZEGYM1v8zp5Py7UjFb79S58UeqCL9pYVnVPURKEqvioPROaVAJKKLzvH2rDnI',
    },
    # ---- JS ----
    'jquery': {
        'kind': 'js',
        'path': 'vendor/jquery/jquery-3.6.0.min.js',
        'sha384': 'sha384-vtXRMe3mGCbOeY7l30aIg8H9p3GdeSe4IFlP6G8JMa7o7lXvnz3GFKzPxzJdPfGK',
    },
    'bootstrap-js': {
        'kind': 'js',
        'path': 'vendor/bootstrap-5.3.3/bootstrap.bundle.min.js',
        'sha384': 'sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz',
    },
    'htmx': {
        # unpkg's https://unpkg.com/htmx.org@2.0.4 302s to dist/htmx.min.js;
        # this is that final response body.
        'kind': 'js',
        'path': 'vendor/htmx/htmx-2.0.4.min.js',
        'sha384': 'sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+',
    },
}


def vendor_assets_context_processor():
    return {'vendor_assets': VENDOR_ASSETS}
