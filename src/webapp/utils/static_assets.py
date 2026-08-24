"""Long-lived caching for /static, with content-hash cache busting.

``url_for('static', ...)`` grows a ``?v=<content hash>``; a response carrying
that parameter is declared immutable for a year, and one without it gets an
hour. The rule, the measurements behind it and the gates: CLAUDE.md section 11.

An ``after_request`` header shaper, not a cache this app owns -- hence its
place beside ``security_headers.py`` rather than under ``caching/``. The digest
memo in :func:`init_static_assets` is a stated exception to the
``webapp.caching`` facade: file bytes cannot change inside a running container,
so its correct TTL is infinite and a ``clear()`` would only re-hash identical
files. Not precedent; anything memoising data that can change while the process
runs belongs in the facade.
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


def init_static_assets(app):
    """Wire cache-busting URLs + long cache headers onto ``app``."""
    # Memoised per process, not per request: the digest of a file inside a
    # container image cannot change while that image runs. Populated lazily so
    # startup does not pay for assets a deployment never serves.
    #
    # Deliberately NOT a webapp.caching bucket, and the module docstring says
    # why in full: infinite correct TTL, a clear() that could only do harm, size
    # bounded by file count. Do not copy this exemption for a memo whose data
    # can change while the process runs.
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
