"""The ``samuel_role_*`` tables: roles, their permission rows, and grants to
users, POSIX groups and API keys. DDL: ``scripts/sql/create_samuel_roles.sql``.

SQLAlchemy, ``sam.base`` and the pure catalog only: ``sam/__init__.py``
imports this eagerly. No FKs, app-clock timestamps, names not ids on the
grant (``webapp.utils.rbac`` resolves users and groups by name).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy import Column, DateTime, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.ext.hybrid import hybrid_property

from sam.base import ActiveFlagMixin, Base, SessionMixin
from sam.security.permissions import Permission
from sam.security.rbac_catalog import (SUBJECT_TYPES, CatalogError, GrantDef,
                                       RoleCatalog, RoleDef, build_catalog)
from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES

_NAME_MAX = 40
_SUBJECT_MAX = 35
_USER_MAX = 35
_NOTE_MAX = 255
_FACILITY_MAX = 40

#: Held unscoped by at least one active grant, or the catalog cannot be edited.
GUARD_PERMISSIONS = (Permission.MANAGE_ROLES, Permission.SYSTEM_ADMIN)


class RbacInvariantError(ValueError):
    """A write that would leave nobody able to manage roles, or a bad graph."""


class SamuelRole(Base, ActiveFlagMixin, SessionMixin):
    """A named permission bundle; ``extends_role_id`` folds one parent in."""

    __tablename__ = 'samuel_role'
    __table_args__ = (
        Index('samuel_role_name', 'name', unique=True),
    )

    samuel_role_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(_NAME_MAX), nullable=False)
    description = Column(String(_NOTE_MAX))
    extends_role_id = Column(Integer)
    created_by = Column(String(_USER_MAX), nullable=False)
    creation_time = Column(DateTime, nullable=False)
    modified_by = Column(String(_USER_MAX), nullable=False)
    modified_time = Column(DateTime, nullable=False)

    @classmethod
    def get_by_name(cls, session, name: str) -> Optional['SamuelRole']:
        return session.query(cls).filter(cls.name == name).one_or_none()

    @classmethod
    def create(cls, session, *, name: str, permissions: Iterable[Permission],
               by: str, description: Optional[str] = None,
               extends: Optional['SamuelRole'] = None,
               when: Optional[datetime] = None) -> 'SamuelRole':
        """Raises ``ValueError`` on a bad name, ``RbacInvariantError`` on a cycle."""
        name = _clean_name(name)
        if cls.get_by_name(session, name) is not None:
            raise ValueError(f'role {name!r} already exists')
        now = when or datetime.now()
        row = cls(name=name, description=_clean_note(description),
                  extends_role_id=extends.samuel_role_id if extends else None,
                  created_by=by[:_USER_MAX], creation_time=now,
                  modified_by=by[:_USER_MAX], modified_time=now)
        session.add(row)
        session.flush()
        row.set_permissions(permissions, by=by, when=now, _guard=False)
        return row

    def update(self, *, by: str, description=None, extends=None, clear_extends=False,
               active: Optional[bool] = None, when: Optional[datetime] = None) -> 'SamuelRole':
        """``extends`` is a role or ``None`` to leave it; ``clear_extends`` drops it."""
        if description is not None:
            self.description = _clean_note(description)
        if clear_extends:
            self.extends_role_id = None
        elif extends is not None:
            if extends.samuel_role_id == self.samuel_role_id:
                raise RbacInvariantError('a role cannot extend itself')
            self.extends_role_id = extends.samuel_role_id
        if active is not None:
            self.active = active
        self.modified_by = by[:_USER_MAX]
        self.modified_time = when or datetime.now()
        self.session.flush()
        _check_graph(self.session, guard=active is False or extends is not None)
        return self

    def set_permissions(self, permissions: Iterable[Permission], *, by: str,
                        when: Optional[datetime] = None, _guard: bool = True) -> 'SamuelRole':
        """Replace the direct permission rows; parents are untouched."""
        wanted = {p if isinstance(p, Permission) else Permission(p) for p in permissions}
        session = self.session
        for row in list(self.permission_rows()):
            session.delete(row)
        session.flush()
        for p in sorted(wanted, key=lambda p: p.value):
            session.add(SamuelRolePermission(samuel_role_id=self.samuel_role_id,
                                             permission=p.value))
        self.modified_by = by[:_USER_MAX]
        self.modified_time = when or datetime.now()
        session.flush()
        _check_graph(session, guard=_guard)
        return self

    def permission_rows(self) -> List['SamuelRolePermission']:
        return (self.session.query(SamuelRolePermission)
                .filter(SamuelRolePermission.samuel_role_id == self.samuel_role_id)
                .order_by(SamuelRolePermission.permission).all())

    def direct_permissions(self) -> List[Permission]:
        """Rows whose value is still a Permission; a retired one is skipped."""
        out = []
        for row in self.permission_rows():
            try:
                out.append(Permission(row.permission))
            except ValueError:
                continue
        return out

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f'<SamuelRole {self.name} active={self.active}>'


class SamuelRolePermission(Base):
    """One direct permission of a role. The value is ``Permission.value``."""

    __tablename__ = 'samuel_role_permission'
    __table_args__ = (
        PrimaryKeyConstraint('samuel_role_id', 'permission',
                             name='pk_samuel_role_permission'),
    )

    samuel_role_id = Column(Integer, nullable=False)
    permission = Column(String(64), nullable=False)

    def __str__(self) -> str:
        return self.permission

    def __repr__(self) -> str:
        return f'<SamuelRolePermission {self.samuel_role_id} {self.permission}>'


class SamuelRoleGrant(Base, SessionMixin):
    """A role or one permission handed to a subject, unscoped or for one
    facility. Revocation is a stamp, never a delete: the rows are the history."""

    __tablename__ = 'samuel_role_grant'
    __table_args__ = (
        Index('samuel_role_grant_subject', 'subject_type', 'subject_name'),
    )

    samuel_role_grant_id = Column(Integer, primary_key=True, autoincrement=True)
    subject_type = Column(String(8), nullable=False)
    subject_name = Column(String(_SUBJECT_MAX), nullable=False)
    samuel_role_id = Column(Integer)
    permission = Column(String(64))
    facility_name = Column(String(_FACILITY_MAX))
    note = Column(String(_NOTE_MAX))
    created_by = Column(String(_USER_MAX), nullable=False)
    creation_time = Column(DateTime, nullable=False)
    revoked_by = Column(String(_USER_MAX))
    revoked_at = Column(DateTime)

    @hybrid_property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @is_active.expression
    def is_active(cls):
        return cls.revoked_at.is_(None)

    @classmethod
    def find_active(cls, session, *, subject_type: str, subject_name: str,
                    role: Optional[SamuelRole] = None,
                    permission: Optional[Permission] = None,
                    facility_name: Optional[str] = None) -> Optional['SamuelRoleGrant']:
        q = session.query(cls).filter(
            cls.is_active, cls.subject_type == subject_type,
            cls.subject_name == subject_name,
            cls.samuel_role_id == (role.samuel_role_id if role else None),
            cls.permission == (permission.value if permission else None))
        q = q.filter(cls.facility_name == facility_name) if facility_name \
            else q.filter(cls.facility_name.is_(None))
        return q.first()

    @classmethod
    def create(cls, session, *, subject_type: str, subject_name: str, by: str,
               role: Optional[SamuelRole] = None,
               permission: Optional[Permission] = None,
               facility_name: Optional[str] = None, note: Optional[str] = None,
               when: Optional[datetime] = None) -> 'SamuelRoleGrant':
        """Raises ``ValueError`` on a bad subject or a duplicate active grant."""
        if subject_type not in SUBJECT_TYPES:
            raise ValueError(f'subject_type must be one of {", ".join(SUBJECT_TYPES)}')
        if (role is None) == (permission is None):
            raise ValueError('a grant names exactly one of role, permission')
        subject_name = (subject_name or '').strip()
        if not subject_name or len(subject_name) > _SUBJECT_MAX:
            raise ValueError('subject_name is required')
        facility_name = (facility_name or '').strip() or None
        if cls.find_active(session, subject_type=subject_type, subject_name=subject_name,
                           role=role, permission=permission, facility_name=facility_name):
            raise ValueError('that grant is already active')
        row = cls(subject_type=subject_type, subject_name=subject_name,
                  samuel_role_id=role.samuel_role_id if role else None,
                  permission=permission.value if permission else None,
                  facility_name=facility_name, note=_clean_note(note),
                  created_by=by[:_USER_MAX], creation_time=when or datetime.now())
        session.add(row)
        session.flush()
        return row

    def revoke(self, *, by: str, when: Optional[datetime] = None) -> 'SamuelRoleGrant':
        """Raises ``RbacInvariantError`` if this was the last way to manage roles."""
        if self.revoked_at is not None:
            return self
        self.revoked_by = by[:_USER_MAX]
        self.revoked_at = when or datetime.now()
        self.session.flush()
        try:
            assert_manage_roles_survives(self.session)
        except RbacInvariantError:
            self.revoked_by = self.revoked_at = None
            self.session.flush()
            raise
        return self

    def __str__(self) -> str:
        what = f'role:{self.samuel_role_id}' if self.samuel_role_id else self.permission
        scope = f'@{self.facility_name}' if self.facility_name else ''
        return f'{self.subject_type}:{self.subject_name} {what}{scope}'

    def __repr__(self) -> str:
        return f'<SamuelRoleGrant {self}>'


def _clean_name(name: str) -> str:
    name = (name or '').strip()
    if not name or len(name) > _NAME_MAX or not all(c.isalnum() or c in '_-' for c in name):
        raise ValueError('name is required: letters, digits, _ or -, up to 40 characters')
    return name


def _clean_note(note) -> Optional[str]:
    note = (note or '').strip()
    return note[:_NOTE_MAX] if note else None


def role_defs(session, *, active_only: bool = True) -> Dict[int, RoleDef]:
    """``{role_id: RoleDef}`` over the table; an inactive parent is dropped."""
    q = session.query(SamuelRole)
    if active_only:
        q = q.filter(SamuelRole.is_active)
    roles = q.all()
    by_id = {r.samuel_role_id: r for r in roles}
    perms: Dict[int, set] = {}
    for row in session.query(SamuelRolePermission).all():
        try:
            perms.setdefault(row.samuel_role_id, set()).add(Permission(row.permission))
        except ValueError:
            continue
    return {
        r.samuel_role_id: RoleDef(
            r.name, frozenset(perms.get(r.samuel_role_id, ())),
            extends=by_id[r.extends_role_id].name
            if r.extends_role_id in by_id else None,
            description=r.description or '')
        for r in roles
    }


def grant_defs(session, defs: Dict[int, RoleDef]) -> List[GrantDef]:
    """Active grants as ``GrantDef``; one naming an inactive role is skipped."""
    out = []
    for g in session.query(SamuelRoleGrant).filter(SamuelRoleGrant.is_active).all():
        if g.samuel_role_id is not None:
            if g.samuel_role_id not in defs:
                continue
            out.append(GrantDef(g.subject_type, g.subject_name,
                                role=defs[g.samuel_role_id].name, facility=g.facility_name))
        else:
            try:
                p = Permission(g.permission)
            except ValueError:
                continue
            out.append(GrantDef(g.subject_type, g.subject_name, permission=p,
                                facility=g.facility_name))
    return out


def load_catalog(session) -> RoleCatalog:
    """The tables as a catalog. Raises ``CatalogError`` on a cycle."""
    defs = role_defs(session)
    return build_catalog(defs.values(), grant_defs(session, defs), source='db',
                         loaded_at=datetime.now())


def _check_graph(session, *, guard: bool = True) -> None:
    """Cycle check, and the last-holder check on the writes that can lose one."""
    try:
        load_catalog(session)
    except CatalogError as exc:
        raise RbacInvariantError(str(exc)) from exc
    if guard:
        assert_manage_roles_survives(session)


def assert_manage_roles_survives(session) -> None:
    """At least one active, unscoped user or group grant must still resolve to
    MANAGE_ROLES or SYSTEM_ADMIN, or the catalog cannot be edited again."""
    catalog = load_catalog(session)
    for (subject_type, _), perms in catalog.unscoped.items():
        if subject_type != 'apikey' and any(p in perms for p in GUARD_PERMISSIONS):
            return
    raise RbacInvariantError('that would leave nobody able to manage roles')


def seed_defaults(session, *, by: str = 'cli:seed') -> int:
    """Write the factory defaults; returns the number of roles written, 0 when
    the table already holds any role (nothing is touched then)."""
    if session.query(SamuelRole).first() is not None:
        return 0
    now = datetime.now()
    defs = {d.name: d for d in DEFAULT_ROLES}
    rows: Dict[str, SamuelRole] = {}

    def make(name: str) -> SamuelRole:
        if name not in rows:
            d = defs[name]
            parent = make(d.extends) if d.extends else None
            rows[name] = SamuelRole.create(session, name=name, permissions=d.permissions,
                                           by=by, description=d.description,
                                           extends=parent, when=now)
        return rows[name]

    for name in defs:
        make(name)
    for g in DEFAULT_GRANTS:
        SamuelRoleGrant.create(session, subject_type=g.subject_type,
                               subject_name=g.subject_name, by=by,
                               role=rows[g.role] if g.role else None,
                               permission=g.permission, facility_name=g.facility, when=now)
    session.flush()
    return len(rows)
