#!/usr/bin/env python3
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
import sam.session

from webui.extensions import db
from webui.blueprints.admin import admin_bp, init_admin
from webui.blueprints.auth_bp import bp as auth_bp
from webui.auth.models import AuthUser
from webui.utils.rbac import rbac_context_processor
from sam.core.users import User
from webui.api.v1.projects import bp as api_projects_bp
from webui.api.v1.users import bp as api_users_bp
from webui.api.v1.charges import bp as api_charges_bp


def create_app():
    import os

    app = Flask(__name__)

    # Flask configuration
    app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'

    # Authentication provider configuration
    app.config['AUTH_PROVIDER'] = 'stub'  # Options: 'stub', 'ldap', 'saml'

    # Development role mapping (for read-only database)
    # Maps username -> list of roles
    # Bypasses role/role_user tables during development
    # Comment out or set to {} when role tables are ready
    app.config['DEV_ROLE_MAPPING'] = {
        'benkirk': ['admin'],
        'mtrahan': ['facility_manager'],
        'rory': ['project_lead'],
        'andersnb': ['user'],
    }

    # Flask-SQLAlchemy configuration
    app.config['SQLALCHEMY_DATABASE_URI'] = sam.session.connection_string
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Check if SSL is required (for remote servers)
    require_ssl = os.getenv('SAM_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')

    # Production-ready connection pool configuration
    engine_options = {
        'pool_size': 10,           # Number of connections to maintain
        'max_overflow': 20,        # Additional connections when pool is exhausted
        'pool_pre_ping': True,     # Verify connections before using
        'pool_recycle': 3600,      # Recycle connections after 1 hour
        'echo': False,             # Set to True for SQL debugging
    }

    # Add SSL configuration if required
    if require_ssl:
        engine_options['connect_args'] = {'ssl': {'ssl_disabled': False}}

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

    # Initialize db with app
    db.init_app(app)

    # Make db.session available as app.Session for compatibility
    #app.Session = lambda: db.session

    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'

    @login_manager.user_loader
    def load_user(user_id):
        """Load user by ID for Flask-Login."""
        sam_user = db.session.query(User).filter_by(user_id=int(user_id)).first()
        if sam_user:
            return AuthUser(sam_user, dev_role_mapping=app.config.get('DEV_ROLE_MAPPING'))
        return None

    # Register context processor for RBAC in templates
    app.context_processor(rbac_context_processor)

    # Register teardown handler for automatic session cleanup
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Register API blueprints
    app.register_blueprint(api_projects_bp, url_prefix='/api/v1/projects')
    app.register_blueprint(api_users_bp, url_prefix='/api/v1/users')
    app.register_blueprint(api_charges_bp, url_prefix='/api/v1')

    # Initialize Flask-Admin
    init_admin(app)

    # Home page redirect
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('admin.index'))
        return redirect(url_for('auth.login'))

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5050)
