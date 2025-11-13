# -------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *

# -------------------------------------------------------------------------eh-


# -------------------------------------------------------------------------bm-
# ----------------------------------------------------------------------------
class Organization(Base, TimestampMixin, ActiveFlagMixin):
    """Organizational units (departments, labs, etc.)."""

    __tablename__ = "organization"

    __table_args__ = (
        Index("ix_organization_tree", "tree_left", "tree_right"),
        Index("ix_organization_parent", "parent_org_id"),
    )

    def __eq__(self, other):
        """Two organizations are equal if they have the same organization_id."""
        if not isinstance(other, Organization):
            return False
        return (
            self.organization_id is not None
            and self.organization_id == other.organization_id
        )

    def __hash__(self):
        """Hash based on organization_id for set/dict operations."""
        return (
            hash(self.organization_id)
            if self.organization_id is not None
            else hash(id(self))
        )

    organization_id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    acronym = Column(String(15), nullable=False, unique=True)
    description = Column(String(255))
    parent_org_id = Column(Integer, ForeignKey("organization.organization_id"))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    level = Column(String(80))
    level_code = Column(String(10))

    idms_sync_token = Column(String(64))

    children = relationship(
        "Organization", remote_side=[parent_org_id], back_populates="parent"
    )
    parent = relationship(
        "Organization", remote_side=[organization_id], back_populates="children"
    )
    primary_responsible_resources = relationship(
        "Resource",
        foreign_keys="Resource.prim_responsible_org_id",
        back_populates="prim_responsible_org",
    )
    projects = relationship("ProjectOrganization", back_populates="organization")
    users = relationship("UserOrganization", back_populates="organization")

    def __str__(self):
        return f"{self.name} ({self.acronym})"

    def __repr__(self):
        return f"<Organization(name='{self.name}', acronym='{self.acronym}')>"


# ----------------------------------------------------------------------------
class UserOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps users to organizations."""

    __tablename__ = "user_organization"

    __table_args__ = (
        Index("ix_user_organization_user", "user_id"),
        Index("ix_user_organization_org", "organization_id"),
        Index("ix_user_organization_dates", "start_date", "end_date"),
    )

    user_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    organization_id = Column(
        Integer, ForeignKey("organization.organization_id"), nullable=False
    )

    user = relationship("User", back_populates="organizations")
    organization = relationship("Organization", back_populates="users")


# ============================================================================
# Group Management
# ============================================================================


# ----------------------------------------------------------------------------
class Institution(Base, TimestampMixin):
    """Educational and research institutions."""

    __tablename__ = "institution"

    def __eq__(self, other):
        """Two institutions are equal if they have the same institution_id."""
        if not isinstance(other, Institution):
            return False
        return (
            self.institution_id is not None
            and self.institution_id == other.institution_id
        )

    def __hash__(self):
        """Hash based on institution_id for set/dict operations."""
        return (
            hash(self.institution_id)
            if self.institution_id is not None
            else hash(id(self))
        )

    institution_id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    acronym = Column(String(40), nullable=False)
    nsf_org_code = Column(String(200))
    address = Column(String(255))
    city = Column(String(30))
    zip = Column(String(15))
    code = Column(String(3))
    idms_sync_token = Column(String(64))

    institution_type = relationship("InstitutionType", back_populates="institutions")
    institution_type_id = Column(
        Integer, ForeignKey("institution_type.institution_type_id")
    )
    state_prov = relationship("StateProv", back_populates="institutions")
    state_prov_id = Column(Integer, ForeignKey("state_prov.ext_state_prov_id"))
    users = relationship("UserInstitution", back_populates="institution")

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<Institution(name='{self.name}', acronym='{self.acronym}')>"


# ----------------------------------------------------------------------------
class InstitutionType(Base, TimestampMixin):
    """Types of institutions (University, Government, etc.)."""

    __tablename__ = "institution_type"

    institution_type_id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(45), nullable=False)

    institutions = relationship("Institution", back_populates="institution_type")

    def __str__(self):
        return f"{self.type}"

    def __repr__(self):
        return f"<InstitutionType(type='{self.type}')>"


# ----------------------------------------------------------------------------
class UserInstitution(Base, TimestampMixin, DateRangeMixin):
    """Maps users to institutions."""

    __tablename__ = "user_institution"

    __table_args__ = (
        Index("ix_user_institution_user", "user_id"),
        Index("ix_user_institution_institution", "institution_id"),
        Index("ix_user_institution_dates", "start_date", "end_date"),
    )

    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    institution_id = Column(
        Integer, ForeignKey("institution.institution_id"), nullable=False
    )

    user = relationship("User", back_populates="institutions")
    institution = relationship("Institution", back_populates="users")


# ============================================================================
# Organization Management
# ============================================================================


# ----------------------------------------------------------------------------
class MnemonicCode(Base, TimestampMixin, ActiveFlagMixin):
    """Mnemonic codes for project naming."""

    __tablename__ = "mnemonic_code"

    mnemonic_code_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(3), nullable=False, unique=True)
    description = Column(String(200), nullable=False, unique=True)

    project_codes = relationship("ProjectCode", back_populates="mnemonic_code")

    def __str__(self):
        return f"{self.code} - {self.description}"

    def __repr__(self):
        return f"<MnemonicCode(code='{self.code}', desc='{self.description}')>"


# ----------------------------------------------------------------------------
class ProjectOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps projects to organizations."""

    __tablename__ = "project_organization"

    __table_args__ = (
        Index("ix_project_organization_project", "project_id"),
        Index("ix_project_organization_org", "organization_id"),
        Index("ix_project_organization_dates", "start_date", "end_date"),
    )

    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.project_id"), nullable=False)
    organization_id = Column(
        Integer, ForeignKey("organization.organization_id"), nullable=False
    )

    project = relationship("Project", back_populates="organizations")
    organization = relationship("Organization", back_populates="projects")


# -------------------------------------------------------------------------em-
