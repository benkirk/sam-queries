"""
Flask-Login user wrapper for SAM User model.

This provides a thin adapter between SAM's User model and Flask-Login's requirements.
"""

from flask_login import UserMixin
from sqlalchemy.orm import Session

from sam.core.users import User
from webapp.utils.rbac import GROUP_PERMISSIONS


class AuthUser(UserMixin):
    """
    Flask-Login compatible user wrapper.

    Wraps a SAM User object to provide Flask-Login required methods.
    This allows us to use SAM's existing User model without modification.

    Authorization model
    -------------------
    The user's permissions are derived from POSIX group membership.
    ``self.roles`` is the set of group names the user belongs to that
    have a bundle in ``GROUP_PERMISSIONS`` (i.e. groups that confer
    permissions).

    Group membership comes from ``adhoc_system_account_entry`` via
    ``get_user_group_access()`` — in dev, test, and production alike.
    Per-user incremental grants live in ``USER_PERMISSION_OVERRIDES``.

    The SAM ``role_user`` / ``role`` tables are **not** consulted.
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

    @property
    def roles(self):
        """
        Get group-bundle names the user belongs to, as a set.

        Derived from POSIX group membership (``get_user_group_access``),
        filtered to groups that actually have a ``GROUP_PERMISSIONS``
        bundle — groups conferring no permissions are noise as far as
        RBAC is concerned.

        Cached on the instance.
        """
        if self._roles is None:
            self._roles = {
                g for g in self._posix_group_names() if g in GROUP_PERMISSIONS
            }
        return self._roles

    def _posix_group_names(self) -> set:
        """
        Look up POSIX group names for this user via SAM's
        ``get_user_group_access()`` query.

        Returns the set of unique group names across all access branches
        (we don't filter by branch here — a group bundle applies whenever
        the user is a member of that group on any branch).

        Returns an empty set if no session is bound to the wrapped User
        object (e.g. the User was detached from its session).
        """
        # Late import to avoid circular: AuthUser is imported very early.
        from sam.queries.lookups import get_user_group_access

        session = Session.object_session(self.sam_user)
        if session is None:
            return set()

        rows = get_user_group_access(session, username=self.username).get(self.username, [])
        return {r['group_name'] for r in rows}

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific group bundle."""
        return role_name in self.roles

    def has_any_role(self, *role_names) -> bool:
        """Check if user has any of the specified group bundles."""
        return bool(self.roles.intersection(role_names))

    def __repr__(self):
        return f"<AuthUser(username='{self.username}', roles={self.roles})>"

    # any other attributes delegated to the SAM User object
    def __getattr__(self, name):
        """
        Delegate attribute access to the wrapped sam_user object.

        This allows AuthUser to expose all properties and methods from
        the SAM User model without explicitly defining them.

        Explicit properties defined above (username, full_name, etc.)
        take precedence over this delegation.
        """
        return getattr(self.sam_user, name)
