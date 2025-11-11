#!/usr/bin/env python3
from flask import Flask, redirect, url_for
from sam.session import create_sam_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from blueprints.admin import admin_bp, init_admin

engine, _ = create_sam_engine()
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'

    # Make session available to blueprints
    app.Session = Session

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
