"""
Flask extensions initialization.

This module holds Flask extension instances to avoid circular imports.
Extensions are initialized here but configured in the application factory.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

from webapp.caching import caching

db = SQLAlchemy()

# CSRF protection for all cookie-session state-changing routes. Initialized
# in create_app; routes that authenticate with non-cookie credentials
# (Basic-auth M2M collectors) opt out via @csrf.exempt at the view.
csrf = CSRFProtect()

# DEPRECATED: prefer `from webapp.caching import caching` and use
# `caching.flask.cached(...)` / `caching.flask.memoize(...)`. This alias is
# retained because 40+ existing call sites import `cache` from here.
cache = caching.flask


def user_aware_cache_key() -> str:
    """Cache key keyed on (current user id, path, query string, facility scope,
    chart layout).

    Use as ``@cache.cached(make_cache_key=user_aware_cache_key)`` on any
    response whose rendered output depends on who is logged in — most
    commonly because the response embeds the navbar (which shows the
    current user's name) or because the data shown is user-scoped.

    Without this, caching by URL alone breaks impersonation: the first
    user to populate the cache "wins" and every subsequent visitor
    (including impersonators) gets that user's rendered view back.

    The scope tag partitions cache entries by the user's
    ``VIEW_PROJECTS`` facility scope so two scoped users with disjoint
    facility grants don't collide (``user_id`` already disambiguates
    today, but the scope tag future-proofs against routes where two
    users might legitimately share a user-id slot — e.g. team-role
    impersonation — and makes the dependency explicit).

    The layout tag is why the query string alone is not enough. Charts render
    per-layout, and a full-page route like ``/allocations/projects`` learns its
    layout from a **cookie**, not the URL — so without this, the first visitor
    to warm the cache would decide whether every later visitor got phone-sized
    or desktop-sized pies.

    The theme tag is the same argument with a more visible failure. Today all
    five of these routes emit table/card fragments with no chart SVG and no
    ``data-bs-theme`` of their own — theming reaches them by CSS inheritance
    from the root attribute, which lives in the page shell, not the fragment —
    so strictly the key does not need it. It is here anyway because "no cached
    fragment ever contains a theme-dependent byte" is an invariant that is real
    today and completely invisible tomorrow: add one chart to the allocations
    fragment and one user's dark SVG is served to every light-mode user with
    the same facility scope. That presents as an intermittent *rendering* bug,
    not a caching bug, and would cost far more to chase than the key costs to
    partition. Same reasoning as ``charts/base.py:chart_view`` — make the wrong
    thing inexpressible.

    Routes with no chart in them pay a doubled key space for nothing. That is
    the right trade at five call sites: the cost is a few extra cache entries,
    and the failure it prevents is silent and user-visible.
    """
    from flask import request
    from flask_login import current_user
    from webapp.utils.htmx import read_layout, read_theme
    from webapp.utils.rbac import user_facility_scope, Permission

    user_part = (
        current_user.user_id
        if getattr(current_user, 'is_authenticated', False)
        else 'anon'
    )
    qs = request.query_string.decode('utf-8', errors='replace')
    scope = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if scope is None:
        scope_part = 'all'
    elif not scope:
        scope_part = 'none'
    else:
        # ``user_facility_scope`` returns a ``set`` — iteration order is
        # not stable across processes. ``sorted`` gives a deterministic
        # key so two users with the same scope get the same slot.
        scope_part = ','.join(sorted(scope))
    return (f"u:{user_part}|{request.path}|{qs}|s:{scope_part}"
            f"|l:{read_layout()}|t:{read_theme()}")
