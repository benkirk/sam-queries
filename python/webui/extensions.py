"""
Flask extensions initialization.

This module holds Flask extension instances to avoid circular imports.
Extensions are initialized here but configured in the application factory.
"""

from flask_sqlalchemy import SQLAlchemy

# Initialize Flask-SQLAlchemy extension
# Will be configured with app in create_app()
db = SQLAlchemy()
