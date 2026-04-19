"""Tests for OIDC SSO authentication provider and routes.

Covers:
- OIDCAuthProvider.resolve_user_from_claims() with valid/invalid/missing claims
- Login page rendering: SSO button (OIDC) vs form (stub)
- OIDC login redirect initiation
- Callback flow with mocked Authlib
- Error paths: missing state, token failure, user not in SAM
- Logout with RP-initiated redirect
- Open-redirect prevention on next param
- ProductionConfig validation for missing OIDC env vars
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from webapp.auth.providers import (
    OIDCAuthProvider,
    StubAuthProvider,
    get_auth_provider,
)


# ---------------------------------------------------------------------------
# Provider unit tests
# ---------------------------------------------------------------------------

class TestOIDCAuthProvider:
    """Unit tests for OIDCAuthProvider claim resolution."""

    def test_resolve_user_preferred_username(self, session):
        """Resolves SAM user from preferred_username claim."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu', 'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_resolve_user_email_fallback(self, session):
        """Falls back to email prefix when preferred_username is missing."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'email': 'benkirk@ucar.edu', 'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_resolve_user_custom_claim(self, session):
        """Uses custom username_claim when configured."""
        provider = OIDCAuthProvider(db_session=session, username_claim='sub')
        claims = {'sub': 'benkirk', 'email': 'other@ucar.edu'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_resolve_user_not_found(self, session):
        """Returns None when user doesn't exist in SAM."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'nonexistent_user_xyz', 'sub': '999'}
        user = provider.resolve_user_from_claims(claims)
        assert user is None

    def test_resolve_user_missing_claims(self, session):
        """Returns None when both username and email claims are missing."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is None

    def test_resolve_user_empty_claims(self, session):
        """Returns None for empty claims dict."""
        provider = OIDCAuthProvider(db_session=session)
        user = provider.resolve_user_from_claims({})
        assert user is None

    def test_resolve_user_locked(self, session):
        """Returns None for locked SAM user."""
        from sam.core.users import User
        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip("Test user not in database")

        original_locked = user.locked
        try:
            user.locked = True
            session.flush()

            provider = OIDCAuthProvider(db_session=session)
            result = provider.resolve_user_from_claims({'preferred_username': 'benkirk'})
            assert result is None
        finally:
            user.locked = original_locked
            session.flush()

    def test_resolve_user_inactive(self, session):
        """Returns None for inactive SAM user."""
        from sam.core.users import User
        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip("Test user not in database")

        original_active = user.active
        try:
            user.active = False
            session.flush()

            provider = OIDCAuthProvider(db_session=session)
            result = provider.resolve_user_from_claims({'preferred_username': 'benkirk'})
            assert result is None
        finally:
            user.active = original_active
            session.flush()

    def test_supports_redirect_auth(self, session):
        """OIDC provider supports redirect-based auth."""
        provider = OIDCAuthProvider(db_session=session)
        assert provider.supports_redirect_auth() is True

    def test_authenticate_raises(self, session):
        """Direct authenticate() raises NotImplementedError."""
        provider = OIDCAuthProvider(db_session=session)
        with pytest.raises(NotImplementedError):
            provider.authenticate('user', 'pass')

    def test_supports_password_change_false(self, session):
        """OIDC doesn't support password changes."""
        provider = OIDCAuthProvider(db_session=session)
        assert provider.supports_password_change() is False

    def test_handle_callback_no_userinfo(self, session):
        """handle_callback returns None when token has no userinfo."""
        mock_client = MagicMock()
        mock_client.authorize_access_token.return_value = {}
        provider = OIDCAuthProvider(db_session=session, oauth_client=mock_client)
        result = provider.handle_callback()
        assert result is None

    def test_handle_callback_success(self, session):
        """handle_callback resolves user from token userinfo."""
        mock_client = MagicMock()
        mock_client.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu'}
        }
        provider = OIDCAuthProvider(db_session=session, oauth_client=mock_client)
        result = provider.handle_callback()
        assert result is not None
        assert result.username == 'benkirk'


# ---------------------------------------------------------------------------
# Provider factory tests
# ---------------------------------------------------------------------------

class TestProviderFactory:
    """Test get_auth_provider factory."""

    def test_get_stub_provider(self, session):
        provider = get_auth_provider('stub', db_session=session)
        assert isinstance(provider, StubAuthProvider)

    def test_get_oidc_provider(self, session):
        provider = get_auth_provider('oidc', db_session=session)
        assert isinstance(provider, OIDCAuthProvider)

    def test_stub_no_redirect(self, session):
        provider = get_auth_provider('stub', db_session=session)
        assert provider.supports_redirect_auth() is False

    def test_unknown_provider(self, session):
        with pytest.raises(ValueError, match="Unknown auth provider"):
            get_auth_provider('unknown', db_session=session)

    def test_saml_removed(self, session):
        """SAMLAuthProvider was removed in favor of OIDC."""
        with pytest.raises(ValueError, match="Unknown auth provider"):
            get_auth_provider('saml', db_session=session)


# ---------------------------------------------------------------------------
# Blueprint / route tests
# ---------------------------------------------------------------------------

class TestLoginPageRendering:
    """Test login page renders correctly for stub vs OIDC modes."""

    def test_stub_mode_shows_form(self, client):
        """Stub mode renders username/password form."""
        resp = client.get('/auth/login')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="username"' in html
        assert 'name="password"' in html
        assert 'Development Mode' in html

    def test_stub_mode_no_sso_button(self, client):
        """Stub mode does NOT show SSO button."""
        resp = client.get('/auth/login')
        html = resp.data.decode()
        assert 'Sign in with UCAR SSO' not in html

    def test_oidc_mode_shows_sso_button(self, app):
        """OIDC mode renders SSO button instead of form."""
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/login')
                assert resp.status_code == 200
                html = resp.data.decode()
                assert 'Sign in with UCAR SSO' in html
                assert 'name="username"' not in html
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'

    def test_oidc_mode_hides_dev_warning(self, app):
        """OIDC mode does not show development mode warning."""
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/login')
                html = resp.data.decode()
                assert 'Development Mode' not in html
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'


class TestOIDCLoginRoute:
    """Test /auth/oidc/login redirect initiation."""

    def test_oidc_login_no_oauth_configured(self, client):
        """Returns error flash when OAuth is not initialized."""
        resp = client.get('/auth/oidc/login', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'OIDC is not configured' in html

    def test_oidc_login_redirects_to_idp(self, app):
        """Initiates redirect to IdP when OAuth is configured."""
        mock_oauth = MagicMock()
        mock_redirect_resp = MagicMock()
        mock_redirect_resp.status_code = 302
        mock_redirect_resp.headers = {'Location': 'https://login.microsoftonline.com/authorize'}
        mock_oauth.entra.authorize_redirect.return_value = mock_redirect_resp

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/oidc/login')
                mock_oauth.entra.authorize_redirect.assert_called_once()
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)


class TestOIDCCallbackRoute:
    """Test /auth/oidc/callback token exchange and session creation."""

    def test_callback_no_oauth_configured(self, client):
        """Returns error when OAuth is not initialized."""
        resp = client.get('/auth/oidc/callback', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'OIDC is not configured' in html

    def test_callback_success_creates_session(self, app):
        """Successful callback creates Flask-Login session and redirects to a dashboard."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/oidc/callback')
                assert resp.status_code == 302
                location = resp.headers.get('Location', '')
                assert '/auth/login' not in location, "Should not redirect back to login on success"
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)

    def test_callback_user_not_found(self, app):
        """Callback with unknown user flashes error and redirects to login."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'nonexistent_xyz', 'email': 'nope@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/oidc/callback')
                assert resp.status_code == 302
                assert '/auth/login' in resp.headers.get('Location', '')
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)

    def test_callback_token_exchange_failure(self, app):
        """Callback handles token exchange exceptions gracefully."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.side_effect = Exception("Token exchange failed")

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/oidc/callback')
                assert resp.status_code == 302
                assert '/auth/login' in resp.headers.get('Location', '')
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)


class TestOpenRedirectProtection:
    """Test that next parameter is validated against open redirects."""

    def test_safe_relative_redirect(self, app, session):
        """Relative paths are allowed for next parameter."""
        with app.test_client() as c:
            resp = c.post(
                '/auth/login?next=/dashboard/user',
                data={'username': 'benkirk', 'password': 'test'},
            )
            assert resp.status_code == 302
            assert '/dashboard/user' in resp.headers.get('Location', '')

    def test_blocks_external_url(self, app, session):
        """External URLs in next parameter are rejected."""
        with app.test_client() as c:
            resp = c.post(
                '/auth/login?next=https://evil.com/steal',
                data={'username': 'benkirk', 'password': 'test'},
            )
            assert resp.status_code == 302
            location = resp.headers.get('Location', '')
            assert 'evil.com' not in location

    def test_blocks_protocol_relative(self, app, session):
        """Protocol-relative URLs (//evil.com) are rejected."""
        with app.test_client() as c:
            resp = c.post(
                '/auth/login?next=//evil.com/steal',
                data={'username': 'benkirk', 'password': 'test'},
            )
            assert resp.status_code == 302
            location = resp.headers.get('Location', '')
            assert 'evil.com' not in location


class TestLogout:
    """Test logout behavior for stub and OIDC modes."""

    def test_stub_logout(self, auth_client):
        """Stub logout clears session and redirects to status dashboard."""
        resp = auth_client.get('/auth/logout')
        assert resp.status_code == 302
        assert '/status' in resp.headers.get('Location', '')

    def test_oidc_logout_redirects_to_entra(self, app, session):
        """OIDC logout redirects to Entra end_session_endpoint."""
        mock_oauth = MagicMock()
        mock_oauth.entra.load_server_metadata.return_value = {
            'end_session_endpoint': 'https://login.microsoftonline.com/logout'
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                from sam.core.users import User
                user = User.get_by_username(session, 'benkirk')
                with c.session_transaction() as sess:
                    sess['_user_id'] = str(user.user_id)
                    sess['_fresh'] = True

                resp = c.get('/auth/logout')
                assert resp.status_code == 302
                location = resp.headers.get('Location', '')
                assert 'login.microsoftonline.com/logout' in location
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------

class TestProductionConfigOIDCValidation:
    """Test that ProductionConfig validates OIDC env vars when AUTH_PROVIDER=oidc."""

    def test_missing_oidc_vars_raises(self):
        """ProductionConfig.validate() raises when OIDC vars are missing."""
        from webapp.config import ProductionConfig

        env_overrides = {
            'AUTH_PROVIDER': 'oidc',
            'FLASK_SECRET_KEY': 'a' * 64,
        }
        # Remove OIDC vars to trigger validation error
        for var in ('OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'OIDC_ISSUER'):
            env_overrides[var] = ''

        with patch.dict(os.environ, env_overrides, clear=False):
            # Re-evaluate class attribute based on patched env
            original = ProductionConfig.AUTH_PROVIDER
            ProductionConfig.AUTH_PROVIDER = 'oidc'
            try:
                with pytest.raises(EnvironmentError, match="missing required env vars"):
                    ProductionConfig.validate()
            finally:
                ProductionConfig.AUTH_PROVIDER = original

    def test_stub_mode_no_oidc_validation(self):
        """ProductionConfig.validate() skips OIDC check when AUTH_PROVIDER=stub."""
        from webapp.config import ProductionConfig

        with patch.dict(os.environ, {'FLASK_SECRET_KEY': 'a' * 64, 'AUTH_PROVIDER': 'stub'}):
            original = ProductionConfig.AUTH_PROVIDER
            ProductionConfig.AUTH_PROVIDER = 'stub'
            try:
                ProductionConfig.validate()
            finally:
                ProductionConfig.AUTH_PROVIDER = original


# ---------------------------------------------------------------------------
# UPN claim stripping tests
# ---------------------------------------------------------------------------

class TestUPNClaimStripping:
    """Test that UPN-format preferred_username (user@domain) resolves correctly."""

    def test_upn_with_domain_resolves(self, session):
        """benkirk@ucar.edu resolves to SAM user benkirk."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'benkirk@ucar.edu', 'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_upn_with_different_domain(self, session):
        """benkirk@ncar.ucar.edu also strips to benkirk."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'benkirk@ncar.ucar.edu', 'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_short_username_still_works(self, session):
        """Plain benkirk (no @) still works after stripping logic."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'benkirk', 'sub': '123'}
        user = provider.resolve_user_from_claims(claims)
        assert user is not None
        assert user.username == 'benkirk'

    def test_upn_nonexistent_user(self, session):
        """nobody@ucar.edu still returns None."""
        provider = OIDCAuthProvider(db_session=session)
        claims = {'preferred_username': 'nobody_xyz@ucar.edu', 'sub': '999'}
        user = provider.resolve_user_from_claims(claims)
        assert user is None


# ---------------------------------------------------------------------------
# Group-based role resolution tests
# ---------------------------------------------------------------------------

class TestGroupRoleResolution:
    """Test that AuthUser.roles derives from POSIX groups (or dev_group_mapping
    when supplied). The legacy ``role_user`` table is not consulted."""

    def test_dev_mapping_takes_priority(self, session):
        """When user is in dev_group_mapping, those bundles are used."""
        from webapp.auth.models import AuthUser
        from sam.core.users import User

        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip("Test user not in database")

        auth_user = AuthUser(user, dev_group_mapping={'benkirk': ['admin']})
        assert 'admin' in auth_user.roles

    def test_dev_mapping_filters_unknown_bundles(self, session):
        """Group names with no GROUP_PERMISSIONS bundle are filtered out."""
        from webapp.auth.models import AuthUser
        from sam.core.users import User

        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip("Test user not in database")

        auth_user = AuthUser(
            user, dev_group_mapping={'benkirk': ['admin', 'no_such_bundle']}
        )
        assert auth_user.roles == {'admin'}

    def test_posix_groups_used_when_no_dev_mapping(self, session):
        """Without dev_group_mapping, roles derive from POSIX group lookup."""
        from webapp.auth.models import AuthUser
        from webapp.utils.rbac import GROUP_PERMISSIONS
        from sam.core.users import User
        from sam.queries.lookups import get_user_group_access

        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip("Test user not in database")

        auth_user = AuthUser(user, dev_group_mapping={})
        roles = auth_user.roles
        assert isinstance(roles, set)

        posix_groups = {
            r['group_name']
            for r in get_user_group_access(session, username='benkirk').get('benkirk', [])
        }
        expected = {g for g in posix_groups if g in GROUP_PERMISSIONS}
        assert roles == expected

    def test_empty_roles_when_no_groups(self, session):
        """User with no POSIX groups and no dev mapping gets empty roles."""
        from webapp.auth.models import AuthUser
        from sam.core.users import User
        from sam.queries.lookups import get_user_group_access

        users = session.query(User).filter(User.is_active).all()
        user_no_groups = None
        for u in users:
            if not get_user_group_access(session, username=u.username).get(u.username):
                user_no_groups = u
                break
        if user_no_groups is None:
            pytest.skip("No user without POSIX group memberships found in database")

        auth_user = AuthUser(user_no_groups, dev_group_mapping={})
        assert auth_user.roles == set()


# ---------------------------------------------------------------------------
# OIDC callback session and redirect tests
# ---------------------------------------------------------------------------

class TestOIDCSessionAndRedirect:
    """Test session state and role-based redirect after OIDC callback."""

    def test_callback_sets_session_user_id(self, app):
        """After successful callback, session contains user ID."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                c.get('/auth/oidc/callback')
                with c.session_transaction() as sess:
                    assert '_user_id' in sess
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)

    def test_callback_upn_claim_creates_session(self, app):
        """UPN-format claim (user@domain) still creates a valid session."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk@ucar.edu', 'email': 'benkirk@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                resp = c.get('/auth/oidc/callback')
                assert resp.status_code == 302
                assert '/auth/login' not in resp.headers.get('Location', '')
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)

    def test_callback_respects_oidc_next(self, app):
        """Callback redirects to session['oidc_next'] when set."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                with c.session_transaction() as sess:
                    sess['oidc_next'] = '/dashboard/allocations'

                resp = c.get('/auth/oidc/callback')
                assert resp.status_code == 302
                assert '/dashboard/allocations' in resp.headers.get('Location', '')
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)

    def test_callback_oidc_next_consumed(self, app):
        """oidc_next is removed from session after redirect."""
        mock_oauth = MagicMock()
        mock_oauth.entra.authorize_access_token.return_value = {
            'userinfo': {'preferred_username': 'benkirk', 'email': 'benkirk@ucar.edu'}
        }

        app.extensions['oauth'] = mock_oauth
        app.config['AUTH_PROVIDER'] = 'oidc'
        try:
            with app.test_client() as c:
                with c.session_transaction() as sess:
                    sess['oidc_next'] = '/dashboard/user'

                c.get('/auth/oidc/callback')
                with c.session_transaction() as sess:
                    assert 'oidc_next' not in sess
        finally:
            app.config['AUTH_PROVIDER'] = 'stub'
            app.extensions.pop('oauth', None)
