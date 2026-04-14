"""
Pluggable authentication providers for SAM Web UI.

This module provides different authentication backends that can be swapped
via configuration without code changes.

Available providers:
- StubAuthProvider: Development/testing authentication (accepts any password)
- LDAPAuthProvider: LDAP authentication (future implementation)
- OIDCAuthProvider: OpenID Connect SSO via Authlib (Microsoft Entra, CILogon, etc.)
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional
from sam.core.users import User
from sam.queries.lookups import find_user_by_username

logger = logging.getLogger(__name__)


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    def __init__(self, db_session):
        self.db_session = db_session

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Authenticate user with username/password credentials."""
        pass

    @abstractmethod
    def supports_password_change(self) -> bool:
        """Return True if this provider allows password changes."""
        pass

    def supports_redirect_auth(self) -> bool:
        """Override to True for SSO providers that use redirect-based auth."""
        return False

    def initiate_login(self, redirect_uri: str):
        """SSO providers: return a redirect response to the IdP."""
        raise NotImplementedError("This provider does not support redirect-based auth")

    def handle_callback(self) -> Optional[User]:
        """SSO providers: process the IdP callback and resolve a SAM user."""
        raise NotImplementedError("This provider does not support redirect-based auth")


class StubAuthProvider(AuthProvider):
    """
    Stub authentication provider for development and testing.

    WARNING: Accepts ANY non-empty password for existing users.
    DO NOT use in production!
    """

    def __init__(self, db_session, require_password: str = None):
        super().__init__(db_session)
        self.require_password = require_password

    def authenticate(self, username: str, password: str) -> Optional[User]:
        if self.require_password and password != self.require_password:
            return None
        if not password:
            return None

        user = find_user_by_username(self.db_session, username)
        if user and user.active and not user.locked:
            return user
        return None

    def supports_password_change(self) -> bool:
        return False


class LDAPAuthProvider(AuthProvider):
    """LDAP authentication provider (future implementation)."""

    def __init__(self, db_session, ldap_url: str, base_dn: str, **kwargs):
        super().__init__(db_session)
        self.ldap_url = ldap_url
        self.base_dn = base_dn
        self.config = kwargs

    def authenticate(self, username: str, password: str) -> Optional[User]:
        raise NotImplementedError("LDAP authentication not yet implemented")

    def supports_password_change(self) -> bool:
        return False


class OIDCAuthProvider(AuthProvider):
    """
    OpenID Connect authentication via Authlib.

    Uses redirect-based authorization code flow with PKCE.
    Resolves OIDC claims to existing SAM users (no auto-provisioning).
    """

    def __init__(self, db_session, oauth_client=None, username_claim: str = 'preferred_username'):
        super().__init__(db_session)
        self.oauth_client = oauth_client
        self.username_claim = username_claim

    def authenticate(self, username: str, password: str) -> Optional[User]:
        raise NotImplementedError("OIDC uses redirect-based auth, not password auth")

    def supports_password_change(self) -> bool:
        return False

    def supports_redirect_auth(self) -> bool:
        return True

    def initiate_login(self, redirect_uri: str):
        """Redirect the user to the OIDC IdP authorization endpoint."""
        return self.oauth_client.authorize_redirect(redirect_uri)

    def handle_callback(self) -> Optional[User]:
        """Exchange the authorization code for tokens, validate, and resolve SAM user."""
        token = self.oauth_client.authorize_access_token()
        userinfo = token.get('userinfo', {})
        if not userinfo:
            logger.warning("OIDC callback: no userinfo in token response")
            return None
        return self.resolve_user_from_claims(userinfo)

    def resolve_user_from_claims(self, claims: dict) -> Optional[User]:
        """Map OIDC claims to an existing SAM User.

        Resolution order:
        1. username_claim (default: preferred_username)
        2. email prefix (before @)
        """
        username = claims.get(self.username_claim)
        if username and '@' in username:
            username = username.split('@')[0]
        if not username:
            email = claims.get('email', '')
            username = email.split('@')[0] if email else None

        if not username:
            logger.warning("OIDC claims missing both '%s' and 'email': %s",
                           self.username_claim, list(claims.keys()))
            return None

        user = find_user_by_username(self.db_session, username)
        if not user:
            logger.warning("OIDC login denied: SAM user '%s' not found", username)
            return None
        if not user.active or user.locked:
            logger.warning("OIDC login denied: SAM user '%s' is inactive or locked", username)
            return None

        logger.info("OIDC login success: '%s' resolved from claim '%s'",
                     username, self.username_claim)
        return user


def get_auth_provider(provider_type: str = 'stub', db_session=None, **config):
    """
    Get an authentication provider instance.

    Args:
        provider_type: Type of provider ('stub', 'ldap', 'oidc')
        db_session: SQLAlchemy session
        **config: Provider-specific configuration

    Returns:
        Configured AuthProvider instance
    """
    providers = {
        'stub': StubAuthProvider,
        'ldap': LDAPAuthProvider,
        'oidc': OIDCAuthProvider,
    }

    if provider_type not in providers:
        raise ValueError(
            f"Unknown auth provider: {provider_type}. "
            f"Available: {', '.join(providers.keys())}"
        )

    provider_class = providers[provider_type]
    return provider_class(db_session, **config)
