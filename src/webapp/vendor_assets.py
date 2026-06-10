"""Single source of truth for third-party CDN assets.

PRODUCTION_IMPROVEMENTS item 3 (vendor-asset registry): every external
asset the webapp loads lives here, pinned by version and (where possible)
by Subresource Integrity hash, so templates can't drift from each other —
and a future Content-Security-Policy can be generated from these origins.

SRI hashes were computed at pin time with:

    curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A

(Bootstrap and htmx values cross-checked against their published SRI
strings.) Google Fonts CSS is generated per-User-Agent and CANNOT carry
SRI — its entry has integrity=None and the macro omits the attribute.

Templates render these via the vendor_css()/vendor_js() macros in
templates/fragments/vendor_assets.html; a context processor registered in
run.py exposes `vendor_assets` to every template.
"""

VENDOR_ASSETS = {
    # ---- CSS ----
    'poppins': {
        'kind': 'css',
        'url': 'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap',
        'integrity': None,                # per-UA response, SRI impossible
        'crossorigin': None,
    },
    'bootstrap-css': {
        'kind': 'css',
        'url': 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
        'integrity': 'sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH',
        'crossorigin': 'anonymous',
    },
    'fontawesome-css': {
        'kind': 'css',
        'url': 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css',
        'integrity': 'sha384-PPIZEGYM1v8zp5Py7UjFb79S58UeqCL9pYVnVPURKEqvioPROaVAJKKLzvH2rDnI',
        'crossorigin': 'anonymous',
    },
    # ---- JS ----
    'jquery': {
        'kind': 'js',
        'url': 'https://code.jquery.com/jquery-3.6.0.min.js',
        'integrity': 'sha384-vtXRMe3mGCbOeY7l30aIg8H9p3GdeSe4IFlP6G8JMa7o7lXvnz3GFKzPxzJdPfGK',
        'crossorigin': 'anonymous',
    },
    'bootstrap-js': {
        'kind': 'js',
        'url': 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
        'integrity': 'sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz',
        'crossorigin': 'anonymous',
    },
    'htmx': {
        # unpkg 302s to /htmx.org@2.0.4/dist/htmx.min.js; SRI pins the final
        # response body, so the short URL stays valid and tamper-evident.
        'kind': 'js',
        'url': 'https://unpkg.com/htmx.org@2.0.4',
        'integrity': 'sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+',
        'crossorigin': 'anonymous',
    },
}


def vendor_assets_context_processor():
    return {'vendor_assets': VENDOR_ASSETS}
