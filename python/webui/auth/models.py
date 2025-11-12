"""
Flask-Login user wrapper for SAM User model.

This provides a thin adapter between SAM's User model and Flask-Login's requirements.
"""

from flask_login import UserMixin
from sam.core.users import User


class AuthUser(UserMixin):
    """
    Flask-Login compatible user wrapper.

    Wraps a SAM User object to provide Flask-Login required methods.
    This allows us to use SAM's existing User model without modification.
    """

    def __init__(self, sam_user: User):
        """
        Initialize with a SAM User object.

        Args:
            sam_user: SAM User ORM object
        """
        self.sam_user = sam_user
        self._roles = None

    def get_id(self):
        """Return user ID as string (required by Flask-Login)."""
        return str(self.sam_user.user_id)

    @property
    def is_active(self):
        """Return whether user is active (required by Flask-Login)."""
        return self.sam_user.active and not self.sam_user.locked

    @property
    def is_authenticated(self):
        """Return True if user is authenticated (required by Flask-Login)."""
        return True

    @property
    def is_anonymous(self):
        """Return False - authenticated users are not anonymous."""
        return False

    # Convenience properties to access SAM User attributes
    @property
    def user_id(self):
        return self.sam_user.user_id

    @property
    def username(self):
        return self.sam_user.username

    @property
    def full_name(self):
        return self.sam_user.full_name

    @property
    def display_name(self):
        return self.sam_user.display_name

    @property
    def primary_email(self):
        return self.sam_user.primary_email

    @property
    def roles(self):
        """Get user's role names as a set."""
        if self._roles is None:
            self._roles = {ra.role.name for ra in self.sam_user.role_assignments}
        return self._roles

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role."""
        return role_name in self.roles

    def has_any_role(self, *role_names) -> bool:
        """Check if user has any of the specified roles."""
        return bool(self.roles.intersection(role_names))

    def __repr__(self):
        return f"<AuthUser(username='{self.username}', roles={self.roles})>"
