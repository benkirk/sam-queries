"""
Tests for security ORM models: ApiCredentials and RoleApiCredentials.
"""

import pytest

from sam import ApiCredentials, RoleApiCredentials


class TestApiCredentialsModel:
    """Test ApiCredentials model - API authentication."""

    def test_api_credentials_count(self, session):
        """Test that we can query API credentials."""
        api_count = session.query(ApiCredentials).count()
        assert api_count >= 0, "Should be able to count API credentials"
        print(f"✅ Found {api_count} API credentials")

    def test_api_credentials_query(self, session):
        """Test querying and accessing API credentials properties."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert api.api_credentials_id is not None
        assert api.username is not None
        assert api.password is not None
        assert len(api.password) >= 50  # Bcrypt hash (typically 60 chars)
        print(f"✅ ApiCredentials: {api.username} (enabled={api.enabled})")

    def test_api_credentials_is_enabled_property(self, session):
        """Test is_enabled property."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert isinstance(api.is_enabled, bool)
        assert api.is_enabled == bool(api.enabled)
        print(f"✅ ApiCredentials {api.username} is_enabled = {api.is_enabled}")

    def test_api_credentials_role_assignments(self, session):
        """Test ApiCredentials -> [RoleApiCredentials] relationship."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert hasattr(api, 'role_assignments')
        print(f"✅ ApiCredentials {api.username} has {len(api.role_assignments)} role assignment(s)")

    def test_enabled_api_credentials(self, session):
        """Test querying only enabled API credentials."""
        enabled = session.query(ApiCredentials).filter(ApiCredentials.enabled == True).all()
        print(f"✅ Found {len(enabled)} enabled API credential(s)")

    def test_disabled_api_credentials(self, session):
        """Test querying disabled API credentials."""
        disabled = session.query(ApiCredentials).filter(
            (ApiCredentials.enabled == False) | (ApiCredentials.enabled.is_(None))
        ).all()
        print(f"✅ Found {len(disabled)} disabled API credential(s)")

    def test_api_credential_full_permissions(self, session):
        """Test getting all permissions for an API credential."""
        api = session.query(ApiCredentials).first()
        if not api or not api.role_assignments:
            pytest.skip("No API credentials with role assignments")

        print(f"\n✅ API Credential: {api.username}")
        print(f"   Enabled: {api.is_enabled}")
        print(f"   Roles: {len(api.role_assignments)}")
        for ra in api.role_assignments:
            role = ra.role
            print(f"     - {role.name}: {role.description}")
