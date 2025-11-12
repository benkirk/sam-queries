"""
Pluggable authentication providers for SAM Web UI.

This module provides different authentication backends that can be swapped
via configuration without code changes.

Available providers:
- StubAuthProvider: Development/testing authentication (accepts any password)
- LDAPAuthProvider: LDAP authentication (future implementation)
- SAMLAuthProvider: SAML SSO authentication (future implementation)
"""

from abc import ABC, abstractmethod
from typing import Optional
from sam.core.users import User
from sam.queries import find_user_by_username


class AuthProvider(ABC):
    """
    Abstract base class for authentication providers.

    All authentication providers must implement these methods.
    """

    def __init__(self, db_session):
        """
        Initialize the provider with database session access.

        Args:
            db_session: SQLAlchemy session (Flask-SQLAlchemy db.session)
        """
        self.db_session = db_session

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        Authenticate user credentials.

        Args:
            username: Username to authenticate
            password: Password to verify

        Returns:
            SAM User object if authentication succeeds, None otherwise
        """
        pass

    @abstractmethod
    def supports_password_change(self) -> bool:
        """Return True if this provider allows password changes."""
        pass


class StubAuthProvider(AuthProvider):
    """
    Stub authentication provider for development and testing.

    WARNING: This provider accepts ANY non-empty password for existing users.
    DO NOT use in production!

    Features:
    - Authenticates any existing SAM user with any password
    - Useful for RBAC testing without enterprise auth setup
    - Can be configured to require specific test passwords
    """

    def __init__(self, db_session, require_password: str = None):
        """
        Initialize stub auth provider.

        Args:
            db_session: SQLAlchemy session
            require_password: Optional password to require (default: accept any)
        """
        super().__init__(db_session)
        self.require_password = require_password

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        Stub authentication - accepts any password for existing users.

        Args:
            username: Username to authenticate
            password: Password (ignored in default stub mode)

        Returns:
            User object if user exists in database, None otherwise
        """
        # Check password if required
        if self.require_password and password != self.require_password:
            return None

        # Otherwise just check if password is non-empty
        if not password:
            return None

        # Look up user in SAM database
        user = find_user_by_username(self.db_session, username)

        # Only authenticate active, non-locked users
        if user and user.active and not user.locked:
            return user

        return None

    def supports_password_change(self) -> bool:
        """Stub provider does not support password changes."""
        return False


class LDAPAuthProvider(AuthProvider):
    """
    LDAP authentication provider (future implementation).

    Will support:
    - LDAP bind authentication
    - User attribute syncing
    - Group mapping to SAM roles
    """

    def __init__(self, db_session, ldap_url: str, base_dn: str, **kwargs):
        """
        Initialize LDAP provider.

        Args:
            db_session: SQLAlchemy session
            ldap_url: LDAP server URL (e.g., 'ldap://ldap.example.org')
            base_dn: Base DN for user searches (e.g., 'ou=users,dc=example,dc=org')
            **kwargs: Additional LDAP configuration
        """
        super().__init__(db_session)
        self.ldap_url = ldap_url
        self.base_dn = base_dn
        self.config = kwargs

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        Authenticate against LDAP server.

        TODO: Implement LDAP bind and user sync.
        """
        raise NotImplementedError("LDAP authentication not yet implemented")

    def supports_password_change(self) -> bool:
        """LDAP passwords are managed externally."""
        return False


class SAMLAuthProvider(AuthProvider):
    """
    SAML SSO authentication provider (future implementation).

    Will support:
    - SAML 2.0 assertions
    - Shibboleth integration
    - Attribute-based role mapping
    """

    def __init__(self, db_session, entity_id: str, sso_url: str, **kwargs):
        """
        Initialize SAML provider.

        Args:
            db_session: SQLAlchemy session
            entity_id: SAML entity ID
            sso_url: SSO service URL
            **kwargs: Additional SAML configuration
        """
        super().__init__(db_session)
        self.entity_id = entity_id
        self.sso_url = sso_url
        self.config = kwargs

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        SAML authentication (redirects to SSO, doesn't use username/password directly).

        TODO: Implement SAML assertion validation.
        """
        raise NotImplementedError("SAML authentication not yet implemented")

    def supports_password_change(self) -> bool:
        """SAML uses external identity provider."""
        return False


# Factory function for getting configured provider
def get_auth_provider(provider_type: str = 'stub', db_session=None, **config):
    """
    Get an authentication provider instance.

    Args:
        provider_type: Type of provider ('stub', 'ldap', 'saml')
        db_session: SQLAlchemy session
        **config: Provider-specific configuration

    Returns:
        Configured AuthProvider instance

    Example:
        >>> from webui.extensions import db
        >>> provider = get_auth_provider('stub', db_session=db.session)
        >>> user = provider.authenticate('johndoe', 'any-password')
    """
    providers = {
        'stub': StubAuthProvider,
        'ldap': LDAPAuthProvider,
        'saml': SAMLAuthProvider,
    }

    if provider_type not in providers:
        raise ValueError(
            f"Unknown auth provider: {provider_type}. "
            f"Available: {', '.join(providers.keys())}"
        )

    provider_class = providers[provider_type]
    return provider_class(db_session, **config)
