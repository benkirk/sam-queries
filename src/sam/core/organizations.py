#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Organization(Base, TimestampMixin, ActiveFlagMixin, SessionMixin, NestedSetMixin):
    """Organizational units (departments, labs, etc.)."""
    __tablename__ = 'organization'

    __table_args__ = (
        Index('ix_organization_tree', 'tree_left', 'tree_right'),
        Index('ix_organization_parent', 'parent_org_id'),
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

    organization_id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    acronym = Column(String(15), nullable=False, unique=True)
    description = Column(String(255))
    parent_org_id = Column(Integer, ForeignKey('organization.organization_id'))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    level = Column(String(80))
    level_code = Column(String(10))

    idms_sync_token = Column(String(64))

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

        obj = cls(
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
        Index('ix_user_organization_user', 'user_id'),
        Index('ix_user_organization_org', 'organization_id'),
        Index('ix_user_organization_dates', 'start_date', 'end_date'),
    )

    user_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'), nullable=False)

    user = relationship('User', back_populates='organizations')
    organization = relationship('Organization', back_populates='users')


# ============================================================================
# Group Management
# ============================================================================


#----------------------------------------------------------------------------
class Institution(Base, TimestampMixin, SessionMixin):
    """Educational and research institutions."""
    __tablename__ = 'institution'

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

    institution_id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    acronym = Column(String(40), nullable=False)
    nsf_org_code = Column(String(200))
    address = Column(String(255))
    city = Column(String(30))
    zip = Column(String(15))
    code = Column(String(3))
    idms_sync_token = Column(String(64))

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

        obj = cls(
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

    institution_type_id = Column(Integer, primary_key=True, autoincrement=True)
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

        obj = cls(type=type.strip())
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
        Index('ix_user_institution_user', 'user_id'),
        Index('ix_user_institution_institution', 'institution_id'),
        Index('ix_user_institution_dates', 'start_date', 'end_date'),
    )

    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    institution_id = Column(Integer, ForeignKey('institution.institution_id'), nullable=False)

    user = relationship('User', back_populates='institutions')
    institution = relationship('Institution', back_populates='users')


# ============================================================================
# Organization Management
# ============================================================================


#----------------------------------------------------------------------------
class MnemonicCode(Base, TimestampMixin, ActiveFlagMixin):
    """Mnemonic codes for project naming."""
    __tablename__ = 'mnemonic_code'

    mnemonic_code_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(3), nullable=False, unique=True)
    description = Column(String(200), nullable=False, unique=True)

    project_codes = relationship('ProjectCode', back_populates='mnemonic_code')

    @classmethod
    def build_lookup(cls, session) -> dict:
        """Return a {description: code} dict for all active mnemonic codes.

        Intended as the single fetch for bulk resolution — call once, then
        pass the result to ``resolve_for_institution`` / ``resolve_for_org``.
        """
        return {
            mc.description: mc.code
            for mc in session.query(cls).filter(cls.is_active).all()
        }

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
            result = lookup.get(f"{inst.name}, {inst.city}")
            if result:
                return result
        return lookup.get(inst.name)

    @staticmethod
    def resolve_for_organization(org, lookup: dict) -> str | None:
        """Resolve the mnemonic code for an Organization using the soft-link strategy.

        Mirrors the legacy Java UserOrganizationStrategy: matches on name only.

        Args:
            org: Organization ORM instance (needs .name attribute).
            lookup: dict returned by ``build_lookup()``.

        Returns:
            3-letter mnemonic string, or None if no match.
        """
        return lookup.get(org.name)

    def __str__(self):
        return f"{self.code} - {self.description}"

    def __repr__(self):
        return f"<MnemonicCode(code='{self.code}', desc='{self.description}')>"


#----------------------------------------------------------------------------
class ProjectOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps projects to organizations."""
    __tablename__ = 'project_organization'

    __table_args__ = (
        Index('ix_project_organization_project', 'project_id'),
        Index('ix_project_organization_org', 'organization_id'),
        Index('ix_project_organization_dates', 'start_date', 'end_date'),
    )

    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'),
                            nullable=False)

    project = relationship('Project', back_populates='organizations')
    organization = relationship('Organization', back_populates='projects')


#-------------------------------------------------------------------------em-
