"""Plain-dict reports over the role catalog: the CLI's JSON envelopes, its rich
renderers and the Roles page's Check tab all read these, never an ORM object."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from sam.security.permissions import Permission
from sam.security.rbac_catalog import RoleCatalog, build_catalog
from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES
from sam.security.samuel_roles import (SamuelRole, SamuelRoleGrant, grant_defs,
                                       load_catalog, role_defs)


def config_key_names() -> List[str]:
    """API keys defined by ``API_KEYS_<NAME>`` env vars, as the webapp names them."""
    return sorted(k[9:].lower() for k, v in os.environ.items()
                  if k.startswith('API_KEYS_') and v)


def _grant_dict(g: SamuelRoleGrant, role_names: Dict[int, str]) -> dict:
    return {
        'id': g.samuel_role_grant_id,
        'subject_type': g.subject_type, 'subject_name': g.subject_name,
        'role': role_names.get(g.samuel_role_id) if g.samuel_role_id else None,
        'permission': g.permission,
        'facility': g.facility_name, 'note': g.note,
        'created_by': g.created_by, 'creation_time': g.creation_time.isoformat(),
        'revoked_by': g.revoked_by,
        'revoked_at': g.revoked_at.isoformat() if g.revoked_at else None,
    }


def build_listing(session, *, include_revoked: bool = False) -> dict:
    """Every role with its direct and effective permissions, and every grant."""
    catalog = load_catalog(session)
    roles = session.query(SamuelRole).order_by(SamuelRole.name).all()
    names = {r.samuel_role_id: r.name for r in roles}
    q = session.query(SamuelRoleGrant)
    if not include_revoked:
        q = q.filter(SamuelRoleGrant.is_active)
    grants = q.order_by(SamuelRoleGrant.subject_type, SamuelRoleGrant.subject_name,
                        SamuelRoleGrant.samuel_role_grant_id).all()
    return {
        'kind': 'rbac_listing',
        'roles': [{
            'name': r.name, 'description': r.description, 'active': bool(r.active),
            'extends': names.get(r.extends_role_id) if r.extends_role_id else None,
            'direct': sorted(p.value for p in r.direct_permissions()),
            'effective': sorted(p.value for p in catalog.roles.get(r.name, ())),
        } for r in roles],
        'grants': [_grant_dict(g, names) for g in grants],
    }


def build_effective(session, subject: str) -> dict:
    """What ``user:NAME`` / ``group:NAME`` / ``apikey:NAME`` resolves to, with
    the grants that produced it. A bare name means ``user:``."""
    subject_type, _, name = subject.partition(':')
    if not name:
        subject_type, name = 'user', subject_type
    catalog = load_catalog(session)
    groups: List[str] = []
    if subject_type == 'user':
        from sam.queries.lookups import get_user_group_access
        rows = get_user_group_access(session, username=name).get(name, [])
        groups = sorted({r['group_name'] for r in rows} & catalog.group_subjects)
    subjects = [(subject_type, name)] + [('group', g) for g in groups]
    unscoped = set()
    scoped: Dict[str, set] = {}
    for st, sn in subjects:
        unscoped |= catalog.permissions(st, sn)
        for facility, perms in catalog.facility_permissions(st, sn).items():
            scoped.setdefault(facility, set()).update(perms)
    names = {r.samuel_role_id: r.name for r in session.query(SamuelRole).all()}
    grants = [g for g in session.query(SamuelRoleGrant).filter(SamuelRoleGrant.is_active)
              .order_by(SamuelRoleGrant.samuel_role_grant_id).all()
              if (g.subject_type, g.subject_name) in subjects]
    return {
        'kind': 'rbac_effective',
        'subject_type': subject_type, 'subject_name': name,
        'source': catalog.source, 'groups': groups,
        'unscoped': sorted(p.value for p in unscoped),
        'scoped': {f: sorted(p.value for p in perms) for f, perms in sorted(scoped.items())},
        'grants': [_grant_dict(g, names) for g in grants],
    }


def build_keys(session, *, config_names: Optional[List[str]] = None) -> dict:
    """Every enabled key with its active grant count: the pre-flip checklist."""
    from sam.security.roles import ApiCredentials
    if config_names is None:
        config_names = config_key_names()
    db_names = sorted(c.username for c in session.query(ApiCredentials)
                      .filter(ApiCredentials.enabled).all() if c.username)
    counts: Dict[str, int] = {}
    for g in session.query(SamuelRoleGrant).filter(
            SamuelRoleGrant.is_active, SamuelRoleGrant.subject_type == 'apikey').all():
        counts[g.subject_name] = counts.get(g.subject_name, 0) + 1
    keys = [{'name': n, 'source': 'config', 'grants': counts.get(n, 0)} for n in config_names]
    keys += [{'name': n, 'source': 'db', 'grants': counts.get(n, 0)}
             for n in db_names if n not in config_names]
    return {'kind': 'rbac_keys', 'keys': keys,
            'ungranted': [k['name'] for k in keys if not k['grants']]}


def build_diff(session) -> dict:
    """Roles and grants where the tables and the factory defaults disagree."""
    live = load_catalog(session)
    defaults = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS)
    role_diff = {}
    for name in sorted(set(live.roles) | set(defaults.roles)):
        a, b = live.roles.get(name), defaults.roles.get(name)
        if a != b:
            role_diff[name] = {
                'db': sorted(p.value for p in a) if a is not None else None,
                'defaults': sorted(p.value for p in b) if b is not None else None,
            }
    live_grants = {_grant_key(g) for g in grant_defs(session, role_defs(session))}
    default_grants = {_grant_key(g) for g in DEFAULT_GRANTS}
    return {
        'kind': 'rbac_diff',
        'roles': role_diff,
        'grants_only_in_db': sorted(live_grants - default_grants),
        'grants_only_in_defaults': sorted(default_grants - live_grants),
    }


def _grant_key(g) -> str:
    what = g.role or (g.permission.value if isinstance(g.permission, Permission) else g.permission)
    return f'{g.subject_type}:{g.subject_name} {what}' + (f' @{g.facility}' if g.facility else '')


_CRUD_ACTIONS = ('view', 'edit', 'create', 'delete')


def permission_groups() -> List[tuple]:
    """``[(group label, [(value, label), ...]), ...]`` for a checkbox matrix:
    CRUD families by domain, then everything else under "System"."""
    groups: Dict[str, List[tuple]] = {}
    other: List[tuple] = []
    for p in sorted(Permission, key=lambda p: p.value):
        action, _, domain = p.value.partition('_')
        label = p.value.replace('_', ' ')
        if action in _CRUD_ACTIONS and domain:
            groups.setdefault(domain.replace('_', ' '), []).append((p.value, label))
        else:
            other.append((p.value, label))
    out = [(name, sorted(rows, key=lambda r: _CRUD_ACTIONS.index(r[0].split('_', 1)[0])))
           for name, rows in sorted(groups.items())]
    out.append(('system', other))
    return out
