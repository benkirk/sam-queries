"""
User dashboard blueprint.

Provides dashboard view for regular users to see their projects and allocation spending.
"""

from .blueprint import bp
from . import requests_routes  # noqa: F401  (registers its routes on bp)

__all__ = ['bp']
