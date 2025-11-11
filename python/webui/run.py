#!/usr/bin/env python3
from flask import Flask, redirect, url_for
import sam.session

from webui.extensions import db
from webui.blueprints.admin import admin_bp, init_admin


def create_app():
    app = Flask(__name__)

    # Flask configuration
    app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'

    # Flask-SQLAlchemy configuration
    app.config['SQLALCHEMY_DATABASE_URI'] = sam.session.connection_string
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Production-ready connection pool configuration
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 10,           # Number of connections to maintain
        'max_overflow': 20,        # Additional connections when pool is exhausted
        'pool_pre_ping': True,     # Verify connections before using
        'pool_recycle': 3600,      # Recycle connections after 1 hour
        'echo': False,             # Set to True for SQL debugging
    }

    # Initialize db with app
    db.init_app(app)

    # Make db.session available as app.Session for compatibility
    app.Session = lambda: db.session

    # Register teardown handler for automatic session cleanup
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    # Register blueprints
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Initialize Flask-Admin
    init_admin(app)

    # Home page redirect
    @app.route('/')
    def index():
        return redirect(url_for('admin.index'))

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5050)
