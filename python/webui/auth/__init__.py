"""
Authentication module for SAM Web UI.

Provides pluggable authentication providers (stub, LDAP, SAML, etc.)
and Flask-Login integration.
"""

from .models import AuthUser
from .providers import get_auth_provider

__all__ = ['AuthUser', 'get_auth_provider']
