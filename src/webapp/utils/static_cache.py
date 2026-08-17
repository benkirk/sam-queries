"""Long-lived caching for /static, with content-hash cache busting.

Flask leaves ``SEND_FILE_MAX_AGE_DEFAULT`` at None, which sends
``Cache-Control: no-cache`` on every static file. Browsers cache correctly under
that — but they must *revalidate* on every page load, so each asset costs a
conditional round trip for ever.

Measured on nwc1 2026-08-17, one webapp pod, a 4h37m window (07:28-12:05 MDT):

    4,336 of 4,953 logged requests (87.5%) were /static
    4,152 of those 4,336 (96%)        answered 304 Not Modified
    ~145 revalidations in 4.5h        of vendored bootstrap-5.3.3, an
                                      immutable third-party file

Those 304s carry no body, but each is a full request through gunicorn and
Flask. They were also the dominant consumer of ``max_requests`` in
``containers/webapp/gunicorn_config.py``, whose whole purpose is to bound
*application* heap growth — so the worker-recycle clock was being driven almost
entirely by requests that touch nothing.

The mechanism is two halves that are only correct together:

  * ``url_for('static', filename=...)`` grows a ``?v=<content hash>``
    parameter, so a changed file gets a new URL; and
  * a response carrying that parameter is declared immutable for a year.

**ONE rule decides the header** — is ``?v=`` present — rather than a table of
path prefixes:

  =========== ==============================================
  ``?v=``     ``Cache-Control``
  =========== ==============================================
  present     ``public, max-age=31536000, immutable``
  absent      ``public, max-age=3600``
  =========== ==============================================

Every reference in the templates goes through ``url_for`` (38 of them, and zero
hardcoded ``/static/`` paths — ``test_static_cache.py`` keeps it that way), so
the first branch covers all of the measured traffic. The second exists for the
assets no ``url_for`` can reach: relative ``url()`` targets inside a stylesheet,
i.e. FontAwesome's ``../webfonts/fa-solid-900.woff2`` and the one ``../img/``
reference in the app's own CSS. Those keep a modest TTL rather than an unbounded
one, so replacing such a file *in place* cannot pin a stale copy in somebody's
browser for a year.

The rule is deliberately shaped so that the conservative branch is the
**default**: an asset referenced some novel way gets the short TTL
automatically, and only an explicit ``?v=`` opts into immutability. Getting the
rule wrong therefore costs efficiency, never correctness.

⚠️ One interaction worth knowing. Rendered HTML is itself cached (see
``user_aware_cache_key``), so after a deploy that changes an asset, cached
fragments keep emitting the *old* ``?v=`` until they expire — and a browser
holding that URL keeps using its old copy. Bounded by the HTML cache TTL, and
already covered by the documented post-deploy step,
``sam-admin cache --refresh``. The asset itself is never unreachable: ``v`` is a
cache key, not a lookup key, so any value serves the current file.
"""

import hashlib
from pathlib import Path

from flask import request
from werkzeug.utils import safe_join

# A year is the ceiling RFC 9111 recommends treating as "effectively for ever";
# `immutable` additionally tells browsers not to revalidate even on a manual
# reload, which is the whole point for a hashed URL.
IMMUTABLE_MAX_AGE = 31_536_000
# Long enough to collapse a browsing session's worth of revalidation, short
# enough that replacing an unhashed file in place self-corrects the same day.
UNVERSIONED_MAX_AGE = 3_600

VERSION_PARAM = 'v'
# 8 bytes -> 16 hex chars. Collisions are irrelevant here: a collision between
# two *versions of the same file* would only mean a missed cache invalidation,
# and 2^-64 is far below the rate at which anything else in this path fails.
_DIGEST_SIZE = 8


def asset_version(static_folder, filename):
    """Short content hash of one static file, or None if it cannot be read.

    Content, not mtime. A container rebuild restamps every file's mtime, so an
    mtime tag would invalidate every asset on every deploy — safe, but it would
    discard exactly the cache this module exists to create. A content hash
    keeps an unchanged asset's URL stable across deploys, which is what makes a
    one-year TTL worth having.

    Returns None instead of raising. A template naming a missing asset is
    already a 404 the browser reports; turning it into a 500 on every page that
    mentions it would be a strictly worse failure than a missing cache tag.
    """
    if not static_folder:
        return None
    # safe_join rather than `/`: `filename` is developer-authored today, but it
    # reaches us from url_for kwargs, and this is the one place that would turn
    # a stray '..' into a read outside the static root.
    path = safe_join(str(static_folder), filename)
    if path is None:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.blake2b(data, digest_size=_DIGEST_SIZE).hexdigest()


def init_static_cache(app):
    """Wire cache-busting URLs + long cache headers onto ``app``."""
    # Memoised per process, not per request: the digest of a file inside a
    # container image cannot change while that image runs. Populated lazily so
    # startup does not pay for assets a deployment never serves.
    versions = {}

    def _version(filename):
        if filename not in versions:
            versions[filename] = asset_version(app.static_folder, filename)
        return versions[filename]

    @app.url_defaults
    def _add_static_version(endpoint, values):
        # Only the app's own static endpoint. Flask-Admin registers an
        # `admin.static` served from *its* package directory, so app.static_folder
        # would be the wrong root for it — and it is dev-only anyway
        # (FLASK_ADMIN_ENABLED is off in ProductionConfig).
        if endpoint != 'static':
            return
        filename = values.get('filename')
        if not filename:
            return
        version = _version(filename)
        if version:
            values[VERSION_PARAM] = version

    @app.after_request
    def _set_static_cache_headers(response):
        if request.endpoint != 'static':
            return response
        if request.args.get(VERSION_PARAM):
            value = f'public, max-age={IMMUTABLE_MAX_AGE}, immutable'
        else:
            value = f'public, max-age={UNVERSIONED_MAX_AGE}'
        # Assignment, not setdefault(): Flask has already written `no-cache`
        # here from SEND_FILE_MAX_AGE_DEFAULT=None, and that is precisely the
        # header being replaced. Unlike the security headers, there is no route
        # that wants to override this — only the static endpoint reaches it.
        response.headers['Cache-Control'] = value
        # send_file sets Expires alongside its own Cache-Control. Any value it
        # left is now inconsistent with ours, and an HTTP/1.0 cache would obey
        # the stale one, so drop it and let Cache-Control be the single answer.
        response.headers.pop('Expires', None)
        return response
