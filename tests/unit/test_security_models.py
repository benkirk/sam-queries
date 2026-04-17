"""Tests for security ORM models: ApiCredentials and RoleApiCredentials.

Ported from tests/unit/test_security_models.py. Structural reads only —
verifies columns, types, properties, and relationship shape. Dropped
the decorative print statements from the legacy file.
"""
import pytest

from sam import ApiCredentials


pytestmark = pytest.mark.unit


class TestApiCredentialsModel:

    def test_api_credentials_count(self, session):
        """COUNT query must succeed (may return zero)."""
        assert session.query(ApiCredentials).count() >= 0

    def test_api_credentials_query(self, session):
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")
        assert api.api_credentials_id is not None
        assert api.username is not None
        assert api.password is not None
        # bcrypt hashes are ~60 chars; allow anything >= 50 to tolerate
        # rounds=4 low-cost test hashes and the production rounds=12 format.
        assert len(api.password) >= 50

    def test_is_enabled_property(self, session):
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")
        assert isinstance(api.is_enabled, bool)
        assert api.is_enabled == bool(api.enabled)

    def test_role_assignments_relationship(self, session):
        """ApiCredentials.role_assignments exposes a (possibly empty) list."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")
        assert hasattr(api, 'role_assignments')
        # No assertion on count — may be zero; we only need the attribute.

    def test_enabled_filter(self, session):
        """Filter queries by enabled=True must succeed structurally."""
        enabled = session.query(ApiCredentials).filter(ApiCredentials.enabled == True).all()  # noqa: E712
        assert isinstance(enabled, list)

    def test_disabled_filter(self, session):
        """Filter queries by enabled=False or NULL must succeed structurally."""
        disabled = session.query(ApiCredentials).filter(
            (ApiCredentials.enabled == False) | (ApiCredentials.enabled.is_(None))  # noqa: E712
        ).all()
        assert isinstance(disabled, list)

    def test_full_permissions_traversal(self, session):
        """Walk from API credential → role_assignments → role → role name."""
        api = session.query(ApiCredentials).first()
        if not api or not api.role_assignments:
            pytest.skip("No API credentials with role assignments")
        for ra in api.role_assignments:
            assert ra.role is not None
            assert ra.role.name is not None
