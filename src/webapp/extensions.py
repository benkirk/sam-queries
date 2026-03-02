"""
Flask extensions initialization.

This module holds Flask extension instances to avoid circular imports.
Extensions are initialized here but configured in the application factory.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache

db = SQLAlchemy()
cache = Cache()
