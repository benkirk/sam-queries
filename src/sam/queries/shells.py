"""
Login shell queries for SAM.

The SAM schema stores shell preferences per-(user, resource) via
``user_resource_shell``, with a fallback to ``Resource.default_shell``.
The UI surfaces a single "login shell" per user; these helpers translate
between that surface and the per-resource reality.

Functions:
    active_login_resources: Active HPC + DAV resources (where users log in).
    get_allowable_shell_names: Shells available on every active login resource.
    get_user_current_shell: The user's effective shell name for display.
"""

from collections import Counter
from typing import List, Optional

from sqlalchemy.orm import Session

from sam.core.users import User, UserResourceShell
from sam.resources.resources import Resource, ResourceShell, ResourceType


LOGIN_RESOURCE_TYPES = ('HPC', 'DAV')


def active_login_resources(session: Session) -> List[Resource]:
    """Return active resources whose type is one of LOGIN_RESOURCE_TYPES."""
    return (
        session.query(Resource)
        .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)
        .filter(Resource.is_active)
        .filter(ResourceType.resource_type.in_(LOGIN_RESOURCE_TYPES))
        .order_by(Resource.resource_name)
        .all()
    )


def get_allowable_shell_names(session: Session) -> List[str]:
    """Shells offered by any active login resource that carries a shell catalog.

    In practice, not every active login resource defines a ResourceShell
    catalog — typically only one "anchor" resource does and the rest
    inherit implicitly. A union over the resources that *do* maintain a
    catalog gives the correct set of offerings for the picker. When the
    shell is applied, resources without a matching ResourceShell are
    silently skipped (see ``User.set_login_shell``).
    """
    names = (
        session.query(ResourceShell.shell_name)
        .join(Resource, Resource.resource_id == ResourceShell.resource_id)
        .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)
        .filter(Resource.is_active)
        .filter(ResourceType.resource_type.in_(LOGIN_RESOURCE_TYPES))
        .distinct()
        .all()
    )
    return sorted(n for (n,) in names)


def get_user_current_shell(session: Session, user: User) -> Optional[str]:
    """Return the user's effective shell name for display.

    Preference:
      1. Most common ``shell_name`` among UserResourceShell overrides
         that target an active login resource.
      2. Most common ``default_shell.shell_name`` across active login
         resources (what they'd get with no overrides).
      3. None when neither is available.
    """
    login_resources = active_login_resources(session)
    if not login_resources:
        return None
    login_resource_ids = {r.resource_id for r in login_resources}

    override_names = Counter(
        urs.resource_shell.shell_name
        for urs in user.resource_shells
        if urs.resource_shell is not None
        and urs.resource_shell.resource_id in login_resource_ids
    )
    if override_names:
        return override_names.most_common(1)[0][0]

    default_names = Counter(
        r.default_shell.shell_name
        for r in login_resources
        if r.default_shell is not None
    )
    if default_names:
        return default_names.most_common(1)[0][0]

    return None
