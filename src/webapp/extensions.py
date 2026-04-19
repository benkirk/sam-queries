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
    """Cache key keyed on (current user id, path, query string).

    Use as ``@cache.cached(make_cache_key=user_aware_cache_key)`` on any
    response whose rendered output depends on who is logged in — most
    commonly because the response embeds the navbar (which shows the
    current user's name) or because the data shown is user-scoped.

    Without this, caching by URL alone breaks impersonation: the first
    user to populate the cache "wins" and every subsequent visitor
    (including impersonators) gets that user's rendered view back.
    """
    from flask import request
    from flask_login import current_user

    user_part = (
        current_user.user_id
        if getattr(current_user, 'is_authenticated', False)
        else 'anon'
    )
    qs = request.query_string.decode('utf-8', errors='replace')
    return f"u:{user_part}|{request.path}|{qs}"
