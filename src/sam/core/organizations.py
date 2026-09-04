#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-

import logging
import re

logger = logging.getLogger(__name__)

#: The two orthographic drifts between a hand-kept mnemonic description and its
#: upstream org name (& vs and, Lab vs Laboratory) that the 2026 IdMS lab rename
#: introduced. Normalizing both is injective across active descriptions (guarded
#: below + in test_mnemonic_soft_match); punctuation is deliberately NOT touched —
#: a trailing comma distinguishes real distinct codes (CMU vs CMI).
_MNEMONIC_SOFT_SUBS = ((re.compile(r'\s*&\s*'), ' and '),
                       (re.compile(r'\blaboratory\b'), 'lab'))


class _MnemonicLookup(dict):
    """Exact ``{casefold(description): code}`` map carrying a ``.soft`` fallback
    index only ``resolve_for_organization`` reads. A dict subclass so every
    existing caller (and ``resolve_for_institution``) still sees the exact map."""
    soft: dict


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Organization(Base, TimestampMixin, ActiveFlagMixin, SessionMixin, NestedSetMixin):
    """Organizational units (departments, labs, etc.)."""
    __tablename__ = 'organization'

    __table_args__ = (
        Index('organization_organization_fk', 'parent_org_id'),
    )

    # NestedSetMixin config
    _ns_pk_col = 'organization_id'
    _ns_parent_col = 'parent_org_id'
    _ns_root_col = None      # no tree_root in this model
    _ns_path_attr = 'acronym'

    def __eq__(self, other):
        """Two organizations are equal if they have the same organization_id."""
        if not isinstance(other, Organization):
            return False
        return (self.organization_id is not None and
                self.organization_id == other.organization_id)

    def __hash__(self):
        """Hash based on organization_id for set/dict operations."""
        return (hash(self.organization_id) if self.organization_id is not None
                else hash(id(self)))

    organization_id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String(100), nullable=False)
    acronym = Column(String(15), nullable=False)
    description = Column(String(255))
    parent_org_id = Column(Integer, ForeignKey('organization.organization_id'))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    level = Column(String(80))
    level_code = Column(String(10))

    idms_unique_name = Column(String(64))
    deleted = Column(Boolean)

    children = relationship('Organization', remote_side=[parent_org_id], back_populates='parent')
    parent = relationship('Organization', remote_side=[organization_id], back_populates='children')
    primary_responsible_resources = relationship('Resource', foreign_keys='Resource.prim_responsible_org_id', back_populates='prim_responsible_org')
    projects = relationship('ProjectOrganization', back_populates='organization')
    users = relationship('UserOrganization', back_populates='organization')

    def update(
        self,
        *,
        name: Optional[str] = None,
        acronym: Optional[str] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'Organization':
        """
        Update this Organization record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        NOTE: Never touches tree columns (tree_left, tree_right, level, level_code,
              parent_org_id) — those are managed by the NestedSetMixin.

        Args:
            name: New name (NOT NULL)
            acronym: New acronym (NOT NULL, unique)
            description: New description (nullable — pass empty string to clear)
            active: Whether the organization is active

        Returns:
            self

        Raises:
            ValueError: If required fields are empty
        """
        if name is not None:
            if not name.strip():
                raise ValueError("name is required")
            self.name = name.strip()

        if acronym is not None:
            if not acronym.strip():
                raise ValueError("acronym is required")
            self.acronym = acronym.strip()

        if description is not None:
            self.description = description.strip() if description.strip() else None

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        name: str,
        acronym: str,
        description: Optional[str] = None,
        parent_org_id: Optional[int] = None,
    ) -> 'Organization':
        """
        Create a new Organization and append it as a leaf node.

        The nested-set tree positions (tree_left, tree_right) are managed by the
        NestedSetMixin.  New records are appended at the end of the root level (or
        as children of parent_org_id if supplied).

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        if not acronym or not acronym.strip():
            raise ValueError("acronym is required")

        # organization_id has no AUTO_INCREMENT — compute next value manually
        from sqlalchemy import func
        next_id = (session.query(func.max(cls.organization_id)).scalar() or 0) + 1

        obj = cls(
            organization_id=next_id,
            name=name.strip(),
            acronym=acronym.strip(),
            description=description.strip() if description and description.strip() else None,
            parent_org_id=parent_org_id,
        )
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.name} ({self.acronym})"

    def __repr__(self):
        return f"<Organization(name='{self.name}', acronym='{self.acronym}')>"


#----------------------------------------------------------------------------
class UserOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps users to organizations."""
    __tablename__ = 'user_organization'

    __table_args__ = (
        Index('user_organization_user_fk', 'user_id'),
        Index('user_organization_org_fk', 'organization_id'),
    )

    user_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'), nullable=False)
    idms_unique_name = Column(String(64))

    user = relationship('User', back_populates='organizations')
    organization = relationship('Organization', back_populates='users')

    def __str__(self):
        return f"UserOrganization {self.user_organization_id}: user={self.user_id} / org={self.organization_id}"

    def __repr__(self):
        return f"<UserOrganization(id={self.user_organization_id}, user_id={self.user_id}, org_id={self.organization_id})>"


# ============================================================================
# Group Management
# ============================================================================


#----------------------------------------------------------------------------
class Institution(Base, TimestampMixin, SessionMixin):
    """Educational and research institutions."""
    __tablename__ = 'institution'

    __table_args__ = (
        Index('idx_institution', 'state_prov_id'),
        Index('idx_institution_1', 'institution_type_id'),
    )

    def __eq__(self, other):
        """Two institutions are equal if they have the same institution_id."""
        if not isinstance(other, Institution):
            return False
        return (self.institution_id is not None and
                self.institution_id == other.institution_id)

    def __hash__(self):
        """Hash based on institution_id for set/dict operations."""
        return (hash(self.institution_id) if self.institution_id is not None
                else hash(id(self)))

    institution_id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String(128))
    acronym = Column(String(40), nullable=False)
    deleted = Column(Boolean)
    nsf_org_code = Column(String(200))
    address = Column(String(255))
    city = Column(String(30))
    zip = Column(String(15))
    code = Column(String(3))

    institution_type = relationship('InstitutionType', back_populates='institutions')
    institution_type_id = Column(Integer, ForeignKey('institution_type.institution_type_id'))
    state_prov = relationship('StateProv', back_populates='institutions')
    state_prov_id = Column(Integer, ForeignKey('state_prov.ext_state_prov_id'))
    users = relationship('UserInstitution', back_populates='institution')

    def update(
        self,
        *,
        name: Optional[str] = None,
        acronym: Optional[str] = None,
        nsf_org_code: Optional[str] = None,
        address: Optional[str] = None,
        city: Optional[str] = None,
        zip: Optional[str] = None,
        code: Optional[str] = None,
        institution_type_id: Optional[int] = None,
    ) -> 'Institution':
        """
        Update this Institution record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        NOTE: Institution has no active flag.

        Args:
            name: New name (NOT NULL)
            acronym: New acronym (NOT NULL)
            nsf_org_code: NSF organization code (nullable)
            address: Street address (nullable)
            city: City (nullable)
            zip: ZIP/postal code (nullable)
            code: Short code (nullable, max 3 chars)
            institution_type_id: FK to institution_type (nullable)

        Returns:
            self

        Raises:
            ValueError: If required fields are empty
        """
        if name is not None:
            if not name.strip():
                raise ValueError("name is required")
            self.name = name.strip()

        if acronym is not None:
            if not acronym.strip():
                raise ValueError("acronym is required")
            self.acronym = acronym.strip()

        if nsf_org_code is not None:
            self.nsf_org_code = nsf_org_code.strip() if nsf_org_code.strip() else None

        if address is not None:
            self.address = address.strip() if address.strip() else None

        if city is not None:
            self.city = city.strip() if city.strip() else None

        if zip is not None:
            self.zip = zip.strip() if zip.strip() else None

        if code is not None:
            self.code = code.strip() if code.strip() else None

        if institution_type_id is not None:
            self.institution_type_id = institution_type_id

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        name: str,
        acronym: str,
        nsf_org_code: Optional[str] = None,
        city: Optional[str] = None,
        code: Optional[str] = None,
        institution_type_id: Optional[int] = None,
    ) -> 'Institution':
        """
        Create a new Institution.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        if not acronym or not acronym.strip():
            raise ValueError("acronym is required")

        # institution_id has no AUTO_INCREMENT — compute next value manually
        from sqlalchemy import func
        next_id = (session.query(func.max(cls.institution_id)).scalar() or 0) + 1

        obj = cls(
            institution_id=next_id,
            name=name.strip(),
            acronym=acronym.strip(),
            nsf_org_code=nsf_org_code.strip() if nsf_org_code and nsf_org_code.strip() else None,
            city=city.strip() if city and city.strip() else None,
            code=code.strip() if code and code.strip() else None,
            institution_type_id=institution_type_id,
        )
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<Institution(name='{self.name}', acronym='{self.acronym}')>"


#----------------------------------------------------------------------------
class InstitutionType(Base, TimestampMixin, SessionMixin):
    """Types of institutions (University, Government, etc.)."""
    __tablename__ = 'institution_type'

    institution_type_id = Column(Integer, primary_key=True, autoincrement=False)
    type = Column(String(45), nullable=False)

    institutions = relationship('Institution', back_populates='institution_type')

    def update(
        self,
        *,
        type: Optional[str] = None,
    ) -> 'InstitutionType':
        """
        Update this InstitutionType record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            type: New type name (NOT NULL)

        Returns:
            self

        Raises:
            ValueError: If type name is empty
        """
        if type is not None:
            if not type.strip():
                raise ValueError("type name is required")
            self.type = type.strip()

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        type: str,
    ) -> 'InstitutionType':
        """
        Create a new InstitutionType.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not type or not type.strip():
            raise ValueError("type name is required")

        # institution_type_id has no AUTO_INCREMENT — compute next value manually
        from sqlalchemy import func
        next_id = (session.query(func.max(cls.institution_type_id)).scalar() or 0) + 1

        obj = cls(institution_type_id=next_id, type=type.strip())
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.type}"

    def __repr__(self):
        return f"<InstitutionType(type='{self.type}')>"


#----------------------------------------------------------------------------
class UserInstitution(Base, TimestampMixin, DateRangeMixin):
    """Maps users to institutions."""
    __tablename__ = 'user_institution'

    __table_args__ = (
        Index('user_institution_user_fk', 'user_id'),
        Index('user_institution_inst_fk', 'institution_id'),
    )

    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    institution_id = Column(Integer, ForeignKey('institution.institution_id'), nullable=False)

    user = relationship('User', back_populates='institutions')
    institution = relationship('Institution', back_populates='users')

    def __str__(self):
        return f"UserInstitution {self.user_institution_id}: user={self.user_id} / inst={self.institution_id}"

    def __repr__(self):
        return f"<UserInstitution(id={self.user_institution_id}, user_id={self.user_id}, inst_id={self.institution_id})>"


# ============================================================================
# Organization Management
# ============================================================================


#----------------------------------------------------------------------------
class MnemonicCode(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Mnemonic codes for project naming."""
    __tablename__ = 'mnemonic_code'

    __table_args__ = (
        Index('mnemonic_code_code_uk', 'code', unique=True),
        Index('mnemonic_code_description_uk', 'description', unique=True),
    )

    mnemonic_code_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(3), nullable=False)
    description = Column(String(200), nullable=False)

    project_codes = relationship('ProjectCode', back_populates='mnemonic_code')

    @staticmethod
    def _soft_key(text: str) -> str:
        """Casefolded key tolerant of the &/and and Lab/Laboratory drift only."""
        key = (text or '').casefold()
        for pat, repl in _MNEMONIC_SOFT_SUBS:
            key = pat.sub(repl, key)
        return re.sub(r'\s+', ' ', key).strip()

    @classmethod
    def build_lookup(cls, session) -> '_MnemonicLookup':
        """Active ``{casefold(description): code}`` map — one fetch for bulk
        resolution; pass to ``resolve_for_institution`` / ``resolve_for_organization``.

        Keys casefolded (descriptions drift in capitalization). The result also
        carries a ``.soft`` index that ``resolve_for_organization`` consults on an
        exact miss, tolerant of the &/and + Lab/Laboratory drift between a
        description and its upstream org name; a collision logs and drops the
        ambiguous alias (exact still resolves). Institutions stay exact.
        """
        rows = session.query(cls).filter(cls.is_active).all()
        exact = {mc.description.casefold(): mc.code for mc in rows}
        soft: dict = {}
        collided: set = set()
        for mc in rows:
            key = cls._soft_key(mc.description)
            if key in exact:                        # a real description owns it
                continue
            if soft.get(key, mc.code) != mc.code:
                collided.add(key)
                logger.warning('mnemonic soft-key collision on %r: %s vs %s '
                               '(dropping soft alias; exact match still works)',
                               key, soft[key], mc.code)
            else:
                soft[key] = mc.code
        for key in collided:
            soft.pop(key, None)
        out = _MnemonicLookup(exact)
        out.soft = soft
        return out

    @staticmethod
    def resolve_for_institution(inst, lookup: dict) -> str | None:
        """Resolve the mnemonic code for an Institution using the soft-link strategy.

        Mirrors the legacy Java UserInstitutionStrategy: tries "Name, City"
        first, then falls back to "Name" alone.

        Args:
            inst: Institution ORM instance (needs .name and .city attributes).
            lookup: dict returned by ``build_lookup()``.

        Returns:
            3-letter mnemonic string, or None if no match.
        """
        if inst.city:
            result = lookup.get(f"{inst.name}, {inst.city}".casefold())
            if result:
                return result
        return lookup.get((inst.name or '').casefold())

    @staticmethod
    def resolve_for_organization(org, lookup: dict) -> str | None:
        """Resolve the mnemonic code for an Organization by name, or None.

        Exact match first, then the injective &/and + Lab/Laboratory soft key.
        WARNING: legacy's UserOrganizationStrategy matched the name as a
        *substring* (ilike ANYWHERE); the SAM port narrowed that to exact, which
        broke on the 2026 lab renames. The soft fallback restores that tolerance
        safely (see ``build_lookup``); institutions stay exact, as in legacy.
        """
        name = org.name or ''
        code = lookup.get(name.casefold())
        if code:
            return code
        # The org's normalized key may match either an exact description (which
        # already spells it "and"/"Laboratory") or a soft alias; build_lookup
        # keeps the two key spaces disjoint, so this stays unambiguous.
        soft_key = MnemonicCode._soft_key(name)
        code = lookup.get(soft_key) or (getattr(lookup, 'soft', None) or {}).get(soft_key)
        if code:
            logger.debug('mnemonic for org %r resolved via soft match -> %s '
                         '(description drifted from the org name)', name, code)
        return code

    @classmethod
    def create(cls, session, *, code: str, description: str) -> 'MnemonicCode':
        """Create a new mnemonic code.

        Args:
            session: SQLAlchemy session.
            code: 3-letter uppercase code (e.g. 'UCB').
            description: Matching description string (e.g. 'University of Colorado, Boulder').

        Returns:
            New MnemonicCode instance (flushed, not committed).

        Raises:
            ValueError: if code is not exactly 3 uppercase letters.
        """
        import re
        if not re.fullmatch(r'[A-Z]{3}', code):
            raise ValueError(f"Code must be exactly 3 uppercase letters, got: {code!r}")
        obj = cls(code=code, description=description, active=True)
        session.add(obj)
        session.flush()
        return obj

    def update(self, *, description=None, active=None):
        """Update the description and/or active flag (the 3-letter code is fixed)."""
        if description is not None:
            self.description = description
        if active is not None:
            self.active = active
        self.session.flush()
        return self

    def __str__(self):
        return f"{self.code} - {self.description}"

    def __repr__(self):
        return f"<MnemonicCode(code='{self.code}', desc='{self.description}')>"


#----------------------------------------------------------------------------
class ProjectOrganization(Base, TimestampMixin, DateRangeMixin, SessionMixin):
    """Maps projects to organizations."""
    __tablename__ = 'project_organization'

    __table_args__ = (
        Index('project_organization_proj_fk', 'project_id'),
        Index('project_organization_org_fk', 'organization_id'),
    )

    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'),
                            nullable=False)

    project = relationship('Project', back_populates='organizations')
    organization = relationship('Organization', back_populates='projects')

    @classmethod
    def create(cls, session, *, project_id: int, organization_id: int,
               start_date=None) -> 'ProjectOrganization':
        """Link a project to an organization.

        Does NOT commit; caller must wrap in management_transaction().
        """
        from datetime import datetime
        obj = cls(
            project_id=project_id,
            organization_id=organization_id,
            start_date=start_date or datetime.now(),
        )
        session.add(obj)
        session.flush()
        return obj

    def deactivate(self) -> 'ProjectOrganization':
        """End this project-organization link by setting end_date to now.

        Does NOT commit; caller must wrap in management_transaction().
        """
        from datetime import datetime
        self.end_date = datetime.now()
        self.session.flush()
        return self

    def __str__(self):
        return f"ProjectOrganization {self.project_organization_id}: project={self.project_id} / org={self.organization_id}"

    def __repr__(self):
        return f"<ProjectOrganization(id={self.project_organization_id}, project_id={self.project_id}, org_id={self.organization_id})>"


#-------------------------------------------------------------------------em-
