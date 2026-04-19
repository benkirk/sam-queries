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

    In production, group membership comes from
    ``adhoc_system_account_entry`` via ``get_user_group_access()``.
    In dev/test, ``dev_group_mapping`` (passed by the Flask app's
    ``DEV_GROUP_MAPPING`` config) supplies synthetic group names per
    username, bypassing the database lookup.

    The SAM ``role_user`` / ``role`` tables are **not** consulted.
    """

    def __init__(self, sam_user: User, dev_group_mapping: dict = None):
        """
        Initialize with a SAM User object.

        Args:
            sam_user: SAM User ORM object
            dev_group_mapping: Optional dict mapping username -> list of
                group names. Bypasses the POSIX group lookup; used in
                dev/test where the database may be read-only or the
                user's real adhoc-group membership doesn't reflect the
                permissions we want to grant for testing.
                Example: {'admin_user': ['admin'], 'test_user': ['user']}
        """
        self.sam_user = sam_user
        self.dev_group_mapping = dev_group_mapping or {}
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

        Only includes groups that have a bundle in ``GROUP_PERMISSIONS``
        (groups conferring no permissions are filtered out — they're
        irrelevant to authorization).

        Priority:
        1. ``dev_group_mapping`` if the username has an entry there
           (used in dev/test to bypass the DB)
        2. POSIX group membership from ``get_user_group_access()``
        3. Empty set (no group bundles matched)

        The result is cached on the instance.
        """
        if self._roles is None:
            if self.username in self.dev_group_mapping:
                candidate_groups = set(self.dev_group_mapping[self.username])
            else:
                candidate_groups = self._posix_group_names()

            # Filter to bundle-conferring groups. Other group memberships
            # are noise as far as RBAC is concerned.
            self._roles = {g for g in candidate_groups if g in GROUP_PERMISSIONS}

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
