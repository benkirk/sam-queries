"""
Admin dashboard blueprint.

Provides admin functionality for managing users and projects, including:
- User impersonation
- Project search
- Allocation expirations tracking
"""

from .blueprint import bp

__all__ = ['bp']
