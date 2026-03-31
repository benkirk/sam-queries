"""
Authentication module for SAM Web UI.

Provides pluggable authentication providers (stub, LDAP, OIDC)
and Flask-Login integration.
"""

from .models import AuthUser
from .providers import get_auth_provider, OIDCAuthProvider
from .blueprint import bp

__all__ = ['AuthUser', 'get_auth_provider', 'OIDCAuthProvider', 'bp']
