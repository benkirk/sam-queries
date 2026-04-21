"""
Flask extensions initialization.

This module holds Flask extension instances to avoid circular imports.
Extensions are initialized here but configured in the application factory.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache

db = SQLAlchemy()
cache = Cache()


def user_aware_cache_key() -> str:
    """Cache key keyed on (current user id, path, query string, facility scope).

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
    """
    from flask import request
    from flask_login import current_user
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
    return f"u:{user_part}|{request.path}|{qs}|s:{scope_part}"
