"""Role catalog: bundles, grants and their closure, independent of storage.

A ``RoleDef`` is a named permission set that may extend one parent; a
``GrantDef`` hands a role or a single permission to a subject (user, POSIX
group or API key), unscoped or for one facility. ``build_catalog`` resolves
both into a frozen ``RoleCatalog`` the predicates in ``webapp.utils.rbac``
read. The same code serves the code-side defaults and the ``samuel_role_*``
rows, which is what makes the two comparable. No ORM, no Flask here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Set, Tuple

from sam.security.permissions import Permission

SUBJECT_TYPES = ('user', 'group', 'apikey')
ALL_PERMISSIONS: FrozenSet[Permission] = frozenset(Permission)

Subject = Tuple[str, str]


class CatalogError(ValueError):
    """A role graph or grant that cannot be resolved."""


@dataclass(frozen=True)
class RoleDef:
    name: str
    permissions: FrozenSet[Permission]
    extends: Optional[str] = None
    description: str = ''


@dataclass(frozen=True)
class GrantDef:
    subject_type: str
    subject_name: str
    role: Optional[str] = None
    permission: Optional[Permission] = None
    facility: Optional[str] = None

    def __post_init__(self):
        if self.subject_type not in SUBJECT_TYPES:
            raise CatalogError(f'unknown subject type {self.subject_type!r}')
        if (self.role is None) == (self.permission is None):
            raise CatalogError('a grant names exactly one of role, permission')


def close_permissions(perms: Iterable[Permission]) -> FrozenSet[Permission]:
    """SYSTEM_ADMIN implies every permission; the one rule the catalog adds."""
    perms = frozenset(perms)
    return ALL_PERMISSIONS if Permission.SYSTEM_ADMIN in perms else perms


def expand_roles(defs: Iterable[RoleDef]) -> Dict[str, FrozenSet[Permission]]:
    """Effective permissions per role name, parents folded in."""
    by_name = {d.name: d for d in defs}
    out: Dict[str, FrozenSet[Permission]] = {}

    def close(name: str, chain: Tuple[str, ...]) -> FrozenSet[Permission]:
        if name in out:
            return out[name]
        role = by_name.get(name)
        if role is None:
            raise CatalogError(f'role {chain[-1]!r} extends unknown role {name!r}')
        if name in chain:
            raise CatalogError('role cycle: ' + ' -> '.join(chain + (name,)))
        perms: Set[Permission] = set(role.permissions)
        if role.extends:
            perms |= close(role.extends, chain + (name,))
        out[name] = close_permissions(perms)
        return out[name]

    for name in by_name:
        close(name, ())
    return out


@dataclass(frozen=True)
class RoleCatalog:
    """What every subject holds, resolved. ``unscoped`` and ``scoped`` are keyed
    by ``(subject_type, subject_name)``; ``scoped`` values map facility -> set."""

    roles: Mapping[str, FrozenSet[Permission]]
    unscoped: Mapping[Subject, FrozenSet[Permission]]
    scoped: Mapping[Subject, Mapping[str, FrozenSet[Permission]]]
    source: str = 'defaults'
    loaded_at: Optional[datetime] = None

    def permissions(self, subject_type: str, name: str) -> FrozenSet[Permission]:
        return self.unscoped.get((subject_type, name), frozenset())

    def facility_permissions(self, subject_type: str,
                             name: str) -> Mapping[str, FrozenSet[Permission]]:
        return self.scoped.get((subject_type, name), {})

    @property
    def group_subjects(self) -> FrozenSet[str]:
        """Every POSIX group any grant names, scoped or not."""
        return frozenset(n for t, n in (*self.unscoped, *self.scoped) if t == 'group')

    def subjects(self, subject_type: str) -> FrozenSet[str]:
        return frozenset(n for t, n in (*self.unscoped, *self.scoped) if t == subject_type)

    def as_dicts(self):
        """The three legacy dict shapes, as fresh mutable copies."""
        groups = {n: set(p) for (t, n), p in self.unscoped.items() if t == 'group'}
        users = {n: set(p) for (t, n), p in self.unscoped.items() if t == 'user'}
        facility_users = {
            n: {f: set(p) for f, p in per_fac.items()}
            for (t, n), per_fac in self.scoped.items() if t == 'user'
        }
        return groups, users, facility_users

    @classmethod
    def from_dicts(cls, groups: Mapping[str, Iterable[Permission]],
                   users: Mapping[str, Iterable[Permission]],
                   facility_users: Mapping[str, Mapping[str, Iterable[Permission]]],
                   *, source: str = 'defaults') -> 'RoleCatalog':
        """The inverse of ``as_dicts``; reads the mappings live (no copies)."""
        unscoped = {('group', n): frozenset(p) for n, p in groups.items()}
        unscoped.update({('user', n): frozenset(p) for n, p in users.items()})
        scoped = {('user', n): {f: frozenset(p) for f, p in per_fac.items()}
                  for n, per_fac in facility_users.items()}
        return cls(roles={}, unscoped=unscoped, scoped=scoped, source=source)


def build_catalog(role_defs: Iterable[RoleDef], grant_defs: Iterable[GrantDef], *,
                  source: str = 'defaults',
                  loaded_at: Optional[datetime] = None) -> RoleCatalog:
    """Raises ``CatalogError`` on an unknown role, a cycle or a bad grant."""
    roles = expand_roles(role_defs)
    unscoped: Dict[Subject, Set[Permission]] = {}
    scoped: Dict[Subject, Dict[str, Set[Permission]]] = {}
    for g in grant_defs:
        if g.role is not None:
            if g.role not in roles:
                raise CatalogError(f'grant to {g.subject_type}:{g.subject_name} '
                                   f'names unknown role {g.role!r}')
            perms = roles[g.role]
        else:
            perms = close_permissions({g.permission})
        key = (g.subject_type, g.subject_name)
        if g.facility is None:
            unscoped.setdefault(key, set()).update(perms)
        else:
            scoped.setdefault(key, {}).setdefault(g.facility, set()).update(perms)
    return RoleCatalog(
        roles=roles,
        unscoped={k: frozenset(v) for k, v in unscoped.items()},
        scoped={k: {f: frozenset(p) for f, p in per.items()} for k, per in scoped.items()},
        source=source, loaded_at=loaded_at,
    )
