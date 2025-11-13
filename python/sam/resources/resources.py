# -------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *

# -------------------------------------------------------------------------eh-


# -------------------------------------------------------------------------bm-
# ----------------------------------------------------------------------------
class Resource(Base, TimestampMixin):
    """Computing resources (HPC systems, storage, etc.)."""

    __tablename__ = "resources"

    __table_args__ = (
        Index("ix_resources_type", "resource_type_id"),
        Index("ix_resources_name", "resource_name"),
    )

    def __eq__(self, other):
        """Two resources are equal if they have the same resource_id."""
        if not isinstance(other, Resource):
            return False
        return self.resource_id is not None and self.resource_id == other.resource_id

    def __hash__(self):
        """Hash based on resource_id for set/dict operations."""
        return (
            hash(self.resource_id) if self.resource_id is not None else hash(id(self))
        )

    resource_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_name = Column(String(40), nullable=False, unique=True)
    resource_type_id = Column(
        Integer, ForeignKey("resource_type.resource_type_id"), nullable=False
    )
    description = Column(String(255))
    activity_type = Column(String(12), nullable=False, default="NONE")

    needs_default_project = Column(Boolean, nullable=False, default=False)
    configurable = Column(Boolean, nullable=False, default=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)

    commission_date = Column(DateTime)
    decommission_date = Column(DateTime)

    prim_sys_admin_user_id = Column(Integer, ForeignKey("users.user_id"))
    prim_responsible_org_id = Column(
        Integer, ForeignKey("organization.organization_id")
    )

    default_first_threshold = Column(Integer)
    default_second_threshold = Column(Integer)
    default_home_dir_base = Column(String(255))
    default_resource_shell_id = Column(
        Integer, ForeignKey("resource_shell.resource_shell_id")
    )

    access_branch_resources = relationship(
        "AccessBranchResource", back_populates="resource"
    )
    accounts = relationship("Account", back_populates="resource")
    default_projects = relationship("DefaultProject", back_populates="resource")
    default_shell = relationship(
        "ResourceShell", foreign_keys=[default_resource_shell_id]
    )
    facility_resources = relationship("FacilityResource", back_populates="resource")
    machines = relationship("Machine", back_populates="resource")
    prim_responsible_org = relationship(
        "Organization",
        foreign_keys=[prim_responsible_org_id],
        back_populates="primary_responsible_resources",
    )
    prim_sys_admin = relationship(
        "User",
        foreign_keys=[prim_sys_admin_user_id],
        back_populates="administered_resources",
    )
    queues = relationship("Queue", back_populates="resource")
    resource_type = relationship("ResourceType", back_populates="resources")
    root_directories = relationship(
        "DiskResourceRootDirectory", back_populates="resource"
    )
    shells = relationship(
        "ResourceShell",
        back_populates="resource",
        foreign_keys="ResourceShell.resource_id",
    )
    user_homes = relationship("UserResourceHome", back_populates="resource")
    # xras_hpc_amounts = relationship('XrasHpcAllocationAmount', back_populates='resource')  # REMOVED - XrasHpcAllocationAmountView is read-only
    xras_resource_keys = relationship(
        "XrasResourceRepositoryKeyResource", back_populates="resource"
    )

    @classmethod
    def get_by_name(cls, session, resource_name: str) -> Optional["Resource"]:
        """
        Get a resource by its name.

        Args:
            session: SQLAlchemy session
            resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')

        Returns:
            Resource object if found, None otherwise
        """
        return session.query(cls).filter(cls.resource_name == resource_name).first()

    def is_commissioned_at(self, check_date: Optional[datetime] = None) -> bool:
        """
        Check if resource is commissioned at a given date.

        Args:
            check_date: Date to check (defaults to current datetime)

        Returns:
            True if resource is commissioned at the given date
        """
        if check_date is None:
            check_date = datetime.utcnow()

        if self.commission_date and self.commission_date > check_date:
            return False

        if self.decommission_date and self.decommission_date <= check_date:
            return False

        return True

    @hybrid_property
    def is_commissioned(self) -> bool:
        """
        Check if resource is currently commissioned (Python side).

        Returns:
            True if resource is commissioned and not decommissioned
        """
        return self.is_commissioned_at()

    @is_commissioned.expression
    def is_commissioned(cls):
        """Check if resource is currently commissioned (SQL side)."""
        now = func.now()
        return and_(
            or_(cls.commission_date.is_(None), cls.commission_date <= now),
            or_(cls.decommission_date.is_(None), cls.decommission_date > now),
        )

    @hybrid_property
    def is_active(self) -> bool:
        """
        Check if resource is currently active (Python side).

        A resource is considered active if:
        - It has been commissioned (commission_date is None or in the past)
        - It has not been decommissioned (decommission_date is None or in the future)

        Returns:
            True if resource is active, False otherwise

        Example:
            >>> resource = Resource.get_by_name(session, 'Derecho')
            >>> if resource.is_active:
            ...     print(f"{resource.resource_name} is currently active")
            ... else:
            ...     print(f"{resource.resource_name} is decommissioned")
        """
        now = datetime.utcnow()

        # Check if commissioned
        if self.commission_date and self.commission_date > now:
            return False

        # Check if decommissioned
        if self.decommission_date and self.decommission_date <= now:
            return False

        return True

    @is_active.expression
    def is_active(cls):
        """
        Check if resource is currently active (SQL side).

        This expression version allows filtering in SQL queries:

        Example:
            >>> active_resources = session.query(Resource).filter(
            ...     Resource.is_active
            ... ).all()
        """
        now = func.now()
        return and_(
            or_(cls.commission_date.is_(None), cls.commission_date <= now),
            or_(cls.decommission_date.is_(None), cls.decommission_date > now),
        )

    def __str__(self):
        return f"{self.resource_name} ({self.resource_type.resource_type if self.resource_type else None})"

    def __repr__(self):
        return f"<Resource(name='{self.resource_name}', type='{self.resource_type.resource_type if self.resource_type else None}')>"


# ----------------------------------------------------------------------------
class ResourceType(Base, TimestampMixin, ActiveFlagMixin):
    """Types of resources (HPC, DISK, ARCHIVE, etc.)."""

    __tablename__ = "resource_type"

    resource_type_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(35), nullable=False, unique=True)
    description = Column(String(255))
    grace_period_days = Column(Integer)

    resources = relationship("Resource", back_populates="resource_type")
    factors = relationship("Factor", back_populates="resource_type")
    formulas = relationship("Formula", back_populates="resource_type")

    def __str__(self):
        return f"{self.resource_type}"

    def __repr__(self):
        return f"<ResourceType(type='{self.resource_type}')>"


# ----------------------------------------------------------------------------
class ResourceShell(Base, TimestampMixin):
    """Available shells on resources."""

    __tablename__ = "resource_shell"

    __table_args__ = (Index("ix_resource_shell_resource", "resource_id"),)

    resource_shell_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_id = Column(Integer, ForeignKey("resources.resource_id"), nullable=False)
    shell_name = Column(String(25), nullable=False)
    path = Column(String(1024), nullable=False)

    resource = relationship(
        "Resource", back_populates="shells", foreign_keys=[resource_id]
    )
    user_shells = relationship("UserResourceShell", back_populates="resource_shell")

    def __str__(self):
        return f"{self.shell_name}"

    def __repr__(self):
        return f"<ResourceShell(name='{self.shell_name}', path='{self.path}')>"


# ----------------------------------------------------------------------------
class DiskResourceRootDirectory(Base):
    """Root directories for disk resources with charging exemption flags."""

    __tablename__ = "disk_resource_root_directory"

    __table_args__ = (Index("ix_disk_resource_root_resource", "resource_id"),)

    root_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    root_directory = Column(String(64), nullable=False, unique=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)
    resource_id = Column(Integer, ForeignKey("resources.resource_id"), nullable=False)
    creation_time = Column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.utcnow,
    )
    modified_time = Column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    resource = relationship("Resource", back_populates="root_directories")

    def __str__(self):
        return f"{self.root_directory}"

    def __repr__(self):
        return (
            f"<DiskResourceRootDirectory(path='{self.root_directory}', "
            f"exempt={self.charging_exempt})>"
        )


# ============================================================================
# Archive Activity and Charges
# ============================================================================


# -------------------------------------------------------------------------em-
