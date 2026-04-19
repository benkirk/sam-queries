"""Authentication blueprint for login/logout functionality."""

import logging
from urllib.parse import urlparse

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, current_app, session,
)
from flask_login import login_user, logout_user, login_required, current_user
from webapp.auth.models import AuthUser
from webapp.auth.providers import get_auth_provider, OIDCAuthProvider
from webapp.extensions import db

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__, url_prefix='/auth')


def _is_safe_redirect(target: str) -> bool:
    """Only allow relative paths — block open redirects to external hosts."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _redirect_for_role(auth_user):
    """Redirect to admin or user dashboard based on permissions.

    Gated on the same permission that gates the Admin nav tab so the
    redirect target is always something the user can actually access —
    including users granted admin via USER_PERMISSION_OVERRIDES rather
    than a group bundle.
    """
    from webapp.utils.rbac import has_permission, Permission
    if has_permission(auth_user, Permission.ACCESS_ADMIN_DASHBOARD):
        return redirect(url_for('admin_dashboard.index'))
    return redirect(url_for('user_dashboard.index'))


# ---------------------------------------------------------------------------
# Standard login/logout
# ---------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page: renders SSO button (OIDC) or username/password form (stub)."""
    if current_user.is_authenticated:
        return _redirect_for_role(current_user)

    auth_provider = current_app.config.get('AUTH_PROVIDER', 'stub')
    oidc_enabled = auth_provider == 'oidc'

    if request.method == 'POST' and not oidc_enabled:
        username = request.form.get('username')
        password = request.form.get('password')

        provider = get_auth_provider('stub', db_session=db.session)
        sam_user = provider.authenticate(username, password)

        if sam_user:
            auth_user = AuthUser(sam_user)

            remember = request.form.get('remember', False)
            login_user(auth_user, remember=remember)
            logger.info("Login success (stub): user=%s", username)

            next_page = request.args.get('next')
            if next_page and _is_safe_redirect(next_page):
                return redirect(next_page)
            return _redirect_for_role(auth_user)
        else:
            logger.warning("Login failed (stub): user=%s", username)
            flash('Invalid username or password', 'error')

    # DEV_QUICK_LOGIN_USERS entries are 'username[:LABEL]' strings.
    # Split into (username, label) pairs for the template — label is
    # an optional cosmetic badge, blank for bare usernames.
    test_users = [
        tuple((entry.split(':', 1) + [''])[:2])
        for entry in current_app.config.get('DEV_QUICK_LOGIN_USERS', [])
    ]

    return render_template(
        'auth/login.html',
        test_users=test_users,
        oidc_enabled=oidc_enabled,
    )


# ---------------------------------------------------------------------------
# OIDC SSO routes
# ---------------------------------------------------------------------------

@bp.route('/oidc/login')
def oidc_login():
    """Initiate OIDC authorization code flow — redirect to IdP."""
    oauth = current_app.extensions.get('oauth')
    if not oauth:
        flash('OIDC is not configured.', 'error')
        return redirect(url_for('auth.login'))

    callback_url = current_app.config.get('OIDC_REDIRECT_URI') or url_for(
        'auth.oidc_callback', _external=True
    )

    next_page = request.args.get('next', '')
    if next_page and _is_safe_redirect(next_page):
        session['oidc_next'] = next_page

    logger.info("OIDC login initiated, redirecting to IdP")
    return oauth.entra.authorize_redirect(callback_url)


@bp.route('/oidc/callback')
def oidc_callback():
    """Handle the OIDC IdP callback — exchange code for tokens and create session."""
    oauth = current_app.extensions.get('oauth')
    if not oauth:
        flash('OIDC is not configured.', 'error')
        return redirect(url_for('auth.login'))

    try:
        provider = OIDCAuthProvider(
            db_session=db.session,
            oauth_client=oauth.entra,
            username_claim=current_app.config.get('OIDC_USERNAME_CLAIM', 'preferred_username'),
        )
        sam_user = provider.handle_callback()
    except Exception:
        logger.exception("OIDC callback failed")
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    if not sam_user:
        flash('Your account was not found in SAM or is inactive.', 'error')
        return redirect(url_for('auth.login'))

    auth_user = AuthUser(sam_user)
    login_user(auth_user, remember=False)
    logger.info("OIDC login success: user=%s", sam_user.username)

    next_page = session.pop('oidc_next', None)
    if next_page and _is_safe_redirect(next_page):
        return redirect(next_page)
    return _redirect_for_role(auth_user)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@bp.route('/logout')
@login_required
def logout():
    """Logout: clear Flask session; for OIDC, also redirect to IdP end-session."""
    username = current_user.username
    auth_provider = current_app.config.get('AUTH_PROVIDER', 'stub')
    logout_user()
    flash(f'Logged out {username}.', 'info')
    logger.info("Logout: user=%s provider=%s", username, auth_provider)

    if auth_provider == 'oidc':
        oauth = current_app.extensions.get('oauth')
        if oauth:
            try:
                metadata = oauth.entra.load_server_metadata()
                end_session_url = metadata.get('end_session_endpoint')
                if end_session_url:
                    post_logout_uri = url_for('status_dashboard.index', _external=True)
                    return redirect(f"{end_session_url}?post_logout_redirect_uri={post_logout_uri}")
            except Exception:
                logger.exception("Failed to get OIDC end_session_endpoint")

    return redirect(url_for('status_dashboard.index'))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@bp.route('/profile')
@login_required
def profile():
    """User profile page showing current user's info and roles."""
    return render_template('auth/profile.html', user=current_user)
