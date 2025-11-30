"""
Authentication blueprint for login/logout functionality.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from webapp.auth.models import AuthUser
from webapp.auth.providers import get_auth_provider
from webapp.extensions import db

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Login page and handler.

    GET: Display login form with available test users
    POST: Authenticate user and create session
    """
    # Redirect if already logged in
    if current_user.is_authenticated:
        if 'admin' in current_user.roles:
            return redirect(url_for('admin.index'))
        else:
            return redirect(url_for('user_dashboard.index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Get configured auth provider
        # TODO: Get provider type from config
        provider = get_auth_provider('stub', db_session=db.session)

        # Authenticate
        sam_user = provider.authenticate(username, password)

        if sam_user:
            # Wrap SAM user for Flask-Login with dev role mapping
            dev_role_mapping = current_app.config.get('DEV_ROLE_MAPPING', {})
            auth_user = AuthUser(sam_user, dev_role_mapping=dev_role_mapping)

            # Create session
            remember = request.form.get('remember', False)
            login_user(auth_user, remember=remember)

            # Redirect to next page or appropriate dashboard
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)

            # Redirect based on user role
            if 'admin' in auth_user.roles:
                return redirect(url_for('admin.index'))
            else:
                return redirect(url_for('user_dashboard.index'))
        else:
            flash('Invalid username or password', 'error')

    # Get available test users from DEV_ROLE_MAPPING for quick switching
    dev_role_mapping = current_app.config.get('DEV_ROLE_MAPPING', {})
    test_users = {username: roles for username, roles in dev_role_mapping.items()}

    return render_template('auth/login.html', test_users=test_users)


@bp.route('/logout')
@login_required
def logout():
    """Logout current user and redirect to login (for user switching)."""
    username = current_user.username
    logout_user()
    flash(f'Logged out {username}. Select a different user below to switch.', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/profile')
@login_required
def profile():
    """
    User profile page showing current user's info and roles.
    """
    return render_template('auth/profile.html', user=current_user)
