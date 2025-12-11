#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-

from ..accounting.accounts import *
from ..accounting.adjustments import *
from ..resources.resources import *
from ..summaries.comp_summaries import *
from ..summaries.dav_summaries import *
from ..accounting.calculator import calculate_charges, get_charge_models_for_resource

from sqlalchemy.orm import joinedload

from datetime import timedelta
#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Project(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Research projects."""
    __tablename__ = 'project'

    __table_args__ = (
        Index('ix_project_projcode', 'projcode'),
        Index('ix_project_lead', 'project_lead_user_id'),
        Index('ix_project_admin', 'project_admin_user_id'),
        Index('ix_project_active', 'active'),
        Index('ix_project_tree', 'tree_left', 'tree_right'),
        Index('ix_project_parent', 'parent_id'),
    )

    def __eq__(self, other):
        """Two projects are equal if they have the same project_id."""
        if not isinstance(other, Project):
            return False
        return self.project_id is not None and self.project_id == other.project_id

    def __hash__(self):
        """Hash based on project_id for set/dict operations."""
        return hash(self.project_id) if self.project_id is not None else hash(id(self))

    project_id = Column(Integer, primary_key=True, autoincrement=True)
    projcode = Column(String(30), nullable=False, unique=True, default='')
    title = Column(String(255), nullable=False)
    abstract = Column(Text)

    # Leadership
    project_lead_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    project_admin_user_id = Column(Integer, ForeignKey('users.user_id'))

    # Status flags
    charging_exempt = Column(Boolean, nullable=False, default=False)

    # Foreign keys
    area_of_interest_id = Column(Integer, ForeignKey('area_of_interest.area_of_interest_id'),
                                 nullable=False)
    allocation_type_id = Column(Integer, ForeignKey('allocation_type.allocation_type_id'))
    parent_id = Column(Integer, ForeignKey('project.project_id'))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    tree_root = Column(Integer, ForeignKey('project.project_id'))

    # Unix group
    unix_gid = Column(Integer)
    ext_alias = Column(String(64))

    # Additional timestamps
    membership_change_time = Column(TIMESTAMP)
    inactivate_time = Column(DateTime)

    accounts = relationship('Account', back_populates='project', lazy='selectin', cascade='all')
    admin = relationship('User', foreign_keys=[project_admin_user_id], back_populates='admin_projects')
    allocation_type = relationship('AllocationType', back_populates='projects')
    area_of_interest = relationship('AreaOfInterest', back_populates='projects')
    children = relationship('Project', remote_side=[parent_id], foreign_keys=[parent_id], back_populates='parent')
    contracts = relationship('ProjectContract', back_populates='project', cascade='all, delete-orphan')
    default_projects = relationship('DefaultProject', back_populates='project', cascade='all, delete-orphan')
    directories = relationship('ProjectDirectory', back_populates='project', cascade='save-update, merge')
    lead = relationship('User', foreign_keys=[project_lead_user_id], back_populates='led_projects')
    organizations = relationship('ProjectOrganization', back_populates='project', cascade='all')
    parent = relationship('Project', remote_side=[project_id], foreign_keys=[parent_id], back_populates='children')
    project_number = relationship('ProjectNumber', back_populates='project', uselist=False)

    @classmethod
    def get_active_projects(cls, session, limit: Optional[int] = None) -> List['Project']:
        """
        Get all active, unlocked projects.

        Args:
            session: SQLAlchemy session
            limit: Optional maximum number of results

        Returns:
            List of active Project objects

        Example:
            >>> active_projects = Project.get_active_projects(session, limit=100)
        """
        query = session.query(cls).filter(
            cls.active == True,
            #cls.locked == False
        ).order_by(cls.projcode)

        if limit:
            query = query.limit(limit)

        return query.all()

    @classmethod
    def get_by_projcode(cls, session, projcode: str) -> Optional['Project']:
        """
        Get a project by its exact project code.

        Args:
            session: SQLAlchemy session
            projcode: Exact project code to search for (case-insensitive)

        Returns:
            Project object if found, None otherwise

        Example:
            >>> project = Project.get_by_projcode(session, 'UCSD0001')
            >>> if project:
            ...     print(f"Found: {project.title}")
        """
        return session.query(cls).filter(
            cls.projcode == projcode.upper()
        ).first()

    @classmethod
    def search_by_pattern(cls, session, pattern: str,
                         search_title: bool = True,
                         active_only: bool = True,
                         limit: int = 50) -> List['Project']:
        """
        Search for projects by pattern matching project code or title.

        Args:
            session: SQLAlchemy session
            pattern: Search pattern (supports SQL LIKE wildcards % and _)
            search_title: If True, also search in project titles
            active_only: If True, only return active projects
            limit: Maximum number of results to return

        Returns:
            List of matching Project objects

        Examples:
            >>> # Find all UCSD projects
            >>> projects = Project.search_by_pattern(session, 'UCSD%')

            >>> # Find projects with "climate" in title
            >>> projects = Project.search_by_pattern(session, '%climate%')

            >>> # Search only project codes (not titles)
            >>> projects = Project.search_by_pattern(session, 'N%0001',
            ...                                      search_title=False)

            >>> # Include inactive projects
            >>> projects = Project.search_by_pattern(session, 'TEST%',
            ...                                      active_only=False)
        """
        # Build base query
        query = session.query(cls)

        # Build search conditions
        conditions = [cls.projcode.ilike(pattern)]

        if search_title:
            conditions.append(cls.title.ilike(pattern))

        query = query.filter(or_(*conditions))

        # Apply active filter
        if active_only:
            query = query.filter(cls.active == True)

        # Order by projcode and apply limit
        return query.order_by(cls.projcode).limit(limit).all()

    # # Active account users (filtered join)
    # account_users = relationship(
    #     'AccountUser',
    #     secondary='account',
    #     primaryjoin=(project_id == Account.project_id),
    #     secondaryjoin=and_(
    #         Account.account_id == AccountUser.account_id,
    #         or_(AccountUser.end_date.is_(None), AccountUser.end_date >= func.now())
    #     ),
    #     viewonly=True,
    #     lazy='selectin',
    #     collection_class=set,
    # )

    # @property
    # def users(self) -> List['User']:
    #     """Return a deduplicated list of active users on this project."""
    #     return list({au.user for au in self.account_users if au.user is not None})

    @property
    def active_account_users(self) -> List['AccountUser']:
        """Get currently active account users."""
        now = datetime.now()
        return [
            au for account in self.accounts
            for au in account.users
            if au.end_date is None or au.end_date >= now
        ]

    @property
    def users(self) -> List['User']:
        """Return deduplicated list of active users."""
        return list({au.user for au in self.active_account_users if au.user})

    @property
    def roster(self) -> List['User']:
        """Return the project lead, admin, and any users."""
        s = set(self.users)
        s.add(self.lead)
        if self.admin: s.add(self.admin)
        return list(s)

    def get_user_count(self) -> int:
        """Return the number of active users on this project."""
        return len(self.users)

    def has_user(self, user: 'User') -> bool:
        """Check if a user is active on this project."""
        return user in self.users

    @property
    def active_directories(self) -> List[str]:
        """Return a list of active project directories (if any)."""
        dirs=[]
        if self.directories:
            for d in self.directories:
                if d.is_currently_active:
                    dirs.append(f"{d.directory_name}")
        return dirs

    def get_all_allocations_by_resource(self) -> Dict[str, Optional['Allocation']]:
        """
        Get the most recent active allocation for each resource.

        Returns:
            Dict mapping resource_name to Allocation object
        """
        allocations_by_resource = {}
        now = datetime.now()

        for account in self.accounts:
            if account.resource:
                resource_name = account.resource.resource_name
                active_allocs = [
                    alloc for alloc in account.allocations
                    if alloc.is_active_at(now)
                ]
                if active_allocs:
                    # Get most recent allocation
                    current = max(active_allocs, key=lambda a: a.end_date)
                    allocations_by_resource[resource_name] = current

        return allocations_by_resource

    def get_allocation_by_resource(self, resource_name: str) -> Optional['Allocation']:
        """
        Get the most recent active allocation for a specific resource.

        Args:
            resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')

        Returns:
            Most recent active allocation for that resource, or None
        """
        allocations_by_resource = self.get_all_allocations_by_resource()
        return allocations_by_resource.get(resource_name)

    @hybrid_property
    def has_active_allocations(self) -> bool:
        """Check if project has any active allocations (Python side)."""
        now = datetime.now()
        for account in self.accounts:
            for alloc in account.allocations:
                if alloc.is_active_at(now):
                    return True
        return False

    @has_active_allocations.expression
    def has_active_allocations(cls):
        """Check if project has any active allocations (SQL side)."""
        now = func.now()
        return exists(
            select(1)
            .select_from(Account)
            .join(Allocation)
            .where(
                Account.project_id == cls.project_id,
                Allocation.deleted == False,
                Allocation.start_date <= now,
                or_(Allocation.end_date.is_(None), Allocation.end_date >= now)
            )
        )

    def get_detailed_allocation_usage(self,
                                      resource_name: Optional[str] = None,
                                      include_adjustments: bool = True,
                                      hierarchical: bool = True) -> Dict[str, Dict[str, any]]:
        """
        Calculate allocation usage and remaining balance across all resource types.

        Args:
            resource_name: Optional filter for specific resource (e.g., 'Derecho', 'GLADE')
            include_adjustments: Whether to include manual charge adjustments
            hierarchical: If True, aggregate usage from this project and all descendants (sub-projects).
                          If False, only count usage for this specific project.

        Returns:
            Dict mapping resource_name to usage details.
        """
        now = datetime.now()
        results = {}

        # Check if tree structure is valid for hierarchical queries
        is_tree_valid = bool(self.tree_root and self.tree_left and self.tree_right)
        use_hierarchy = hierarchical and is_tree_valid

        # Get accounts with eager loading
        query = self.session.query(Account).options(joinedload(Account.allocations),
                                                    joinedload(Account.resource).joinedload(Resource.resource_type),
                                                    joinedload(Account.charge_adjustments) if include_adjustments else None
                                                    ).filter(Account.project_id == self.project_id,
                                                             Account.deleted == False
                                                             )

        if resource_name:
            query = query.join(Resource).filter(Resource.resource_name == resource_name)

        for account in query.all():
            if not account.resource:
                continue

            resource = account.resource.resource_name
            resource_type = account.resource.resource_type.resource_type if account.resource.resource_type else 'UNKNOWN'

            # Find active allocation, or most recent if none are active
            query_alloc = None
            for alloc in account.allocations:
                if alloc.is_active_at(now):
                    query_alloc = alloc
                    break

            # No active allocation found - find the most recent one (latest end_date)
            if not query_alloc:
                if account.allocations:
                    most_recent_alloc = max(account.allocations,
                                            key=lambda a: a.end_date if a.end_date else datetime.max
                                            )
                    # Apply date threshold, only query 'most_recent_alloc'
                    # if it has expired within the past 1 year
                    if (now - most_recent_alloc.end_date) < timedelta(days=365):
                        query_alloc = most_recent_alloc

            # OK, if we still don't have an allocation to query
            # then simply skip this account
            if not query_alloc:
                continue

            start_date = query_alloc.start_date
            end_date = query_alloc.end_date or now

            # Determine usage (Charges)
            if use_hierarchy:
                charges_by_type = self.get_subtree_charges(account.resource_id,
                                                           resource_type,
                                                           start_date,
                                                           end_date)
            else:
                charges_by_type = self.get_charges_by_resource_type(account.account_id,
                                                                    resource_type,
                                                                    start_date,
                                                                    end_date)

            # Calculate adjustment total
            adjustments = 0.0
            if include_adjustments:
                if use_hierarchy:
                    adjustments = self.get_subtree_adjustments(account.resource_id,
                                                               start_date,
                                                               end_date)
                else:
                    adjustments = self.get_adjustments(account.account_id,
                                                       start_date,
                                                       end_date)

            # Calculate totals
            allocated = float(query_alloc.amount)
            total_charges = sum(charges_by_type.values())
            effective_used = total_charges + adjustments
            remaining = allocated - effective_used
            percent_used = (effective_used / allocated * 100) if allocated > 0 else 0

            # Calculate time metrics
            days_elapsed = (now - query_alloc.start_date).days
            days_remaining = None
            days_total = None
            if query_alloc.end_date:
                days_remaining = (query_alloc.end_date - now).days
                days_total = (query_alloc.end_date - query_alloc.start_date).days

            # Get job statistics (primarily for HPC/DAV)
            if use_hierarchy:
                total_jobs, total_core_hours = self.get_subtree_job_statistics(account.resource_id,
                                                                               resource_type,
                                                                               start_date,
                                                                               end_date)
            else:
                total_jobs, total_core_hours = self.get_job_statistics(account.account_id,
                                                                       resource_type,
                                                                       start_date,
                                                                       end_date)

            result = {
                'allocation_id': query_alloc.allocation_id,
                'account_id': account.account_id,
                'resource_type': resource_type,
                'allocated': allocated,
                'used': effective_used,
                'remaining': remaining,
                'percent_used': percent_used,
                'charges_by_type': charges_by_type,
                'start_date': query_alloc.start_date,
                'end_date': query_alloc.end_date,
                'days_elapsed': days_elapsed,
                'days_remaining': days_remaining,
                'days_total': days_total,
                'hierarchical': use_hierarchy
            }

            if include_adjustments:
                result['adjustments'] = adjustments

            if total_jobs is not None:
                result['total_jobs'] = total_jobs
                result['total_core_hours'] = total_core_hours

            results[resource] = result

        return results


    def get_charges_by_resource_type(self,
                                     account_id: int,
                                     resource_type: str,
                                     start_date: datetime,
                                     end_date: datetime) -> Dict[str, float]:
        """
        Query appropriate charge summary tables based on resource type (Single Account).

        Returns:
            Dict of charge type to amount, e.g., {'comp': 1000.0, 'disk': 50.0}
        """
        return calculate_charges(self.session, [account_id], start_date, end_date, resource_type)


    def get_subtree_charges(self,
                            resource_id: int,
                            resource_type: str,
                            start_date: datetime,
                            end_date: datetime) -> Dict[str, float]:
        """
        Aggregate charges for this project AND all descendants (subtree) on a specific resource.
        """
        charges = {}
        models = get_charge_models_for_resource(resource_type)

        for key, ModelClass in models.items():
            val = self.session.query(func.coalesce(func.sum(ModelClass.charges), 0))\
                .join(Account, ModelClass.account_id == Account.account_id)\
                .join(Project, Account.project_id == Project.project_id)\
                .filter(
                    Project.tree_root == self.tree_root,
                    Project.tree_left >= self.tree_left,
                    Project.tree_right <= self.tree_right,
                    Account.resource_id == resource_id,
                    ModelClass.activity_date >= start_date,
                    ModelClass.activity_date <= end_date
                ).scalar()

            if val:
                charges[key] = float(val)

        return charges


    def get_adjustments(self,
                        account_id: int,
                        start_date: datetime,
                        end_date: datetime) -> float:
        """Get total charge adjustments for a single account."""
        adj_val = self.session.query(func.coalesce(func.sum(ChargeAdjustment.amount), 0))\
            .filter(
                ChargeAdjustment.account_id == account_id,
                ChargeAdjustment.adjustment_date >= start_date,
                ChargeAdjustment.adjustment_date <= end_date
            ).scalar()
        return float(adj_val)


    def get_subtree_adjustments(self,
                                resource_id: int,
                                start_date: datetime,
                                end_date: datetime) -> float:
        """Get total charge adjustments for the project subtree on a resource."""
        adj_val = self.session.query(func.coalesce(func.sum(ChargeAdjustment.amount), 0))\
            .join(Account, ChargeAdjustment.account_id == Account.account_id)\
            .join(Project, Account.project_id == Project.project_id)\
            .filter(
                Project.tree_root == self.tree_root,
                Project.tree_left >= self.tree_left,
                Project.tree_right <= self.tree_right,
                Account.resource_id == resource_id,
                ChargeAdjustment.adjustment_date >= start_date,
                ChargeAdjustment.adjustment_date <= end_date
            ).scalar()
        return float(adj_val)


    def get_job_statistics(self,
                           account_id: int,
                           resource_type: str,
                           start_date: datetime,
                           end_date: datetime) -> tuple[Optional[int], Optional[float]]:
        """
        Get job count and core hours for computational resources (Single Account).

        Returns:
            Tuple of (total_jobs, total_core_hours) or (None, None)
        """
        if resource_type not in ('HPC', 'DAV'):
            return None, None

        # Use appropriate summary table
        SummaryClass = CompChargeSummary if resource_type == 'HPC' else DavChargeSummary

        stats = self.session.query(func.coalesce(func.sum(SummaryClass.num_jobs), 0).label('jobs'),
                                   func.coalesce(func.sum(SummaryClass.core_hours), 0).label('hours')
                                   ).filter(SummaryClass.account_id == account_id,
                                            SummaryClass.activity_date >= start_date,
                                            SummaryClass.activity_date <= end_date
                                            ).first()

        return int(stats.jobs), float(stats.hours)


    def get_subtree_job_statistics(self,
                                   resource_id: int,
                                   resource_type: str,
                                   start_date: datetime,
                                   end_date: datetime) -> tuple[Optional[int], Optional[float]]:
        """
        Get job count and core hours for computational resources (Subtree Aggregation).
        """
        if resource_type not in ('HPC', 'DAV'):
            return None, None

        SummaryClass = CompChargeSummary if resource_type == 'HPC' else DavChargeSummary

        stats = self.session.query(
                func.coalesce(func.sum(SummaryClass.num_jobs), 0).label('jobs'),
                func.coalesce(func.sum(SummaryClass.core_hours), 0).label('hours')
            ).join(Account, SummaryClass.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == self.tree_root,
                Project.tree_left >= self.tree_left,
                Project.tree_right <= self.tree_right,
                Account.resource_id == resource_id,
                SummaryClass.activity_date >= start_date,
                SummaryClass.activity_date <= end_date
            ).first()

        return int(stats.jobs), float(stats.hours)

    # Tree Navigation Methods (Nested Set Model)
    def get_ancestors(self, include_self: bool = False) -> List['Project']:
        """
        Get all ancestor projects using nested set model.

        Args:
            include_self: Whether to include this project in results

        Returns:
            List of ancestor projects, ordered from root to immediate parent

        Example:
            >>> project.get_ancestors()
            [<Project(root)>, <Project(parent)>]
        """
        if not self.tree_left or not self.tree_right:
            return []

        query = self.session.query(Project).filter(
            and_(
                Project.tree_root == self.tree_root,
                Project.tree_left < self.tree_left,
                Project.tree_right > self.tree_right
            )
        ).order_by(Project.tree_left)

        if include_self:
            ancestors = query.all()
            ancestors.append(self)
            return ancestors

        return query.all()

    def get_descendants(self, include_self: bool = False,
                       max_depth: Optional[int] = None) -> List['Project']:
        """
        Get all descendant projects using nested set model.

        Args:
            include_self: Whether to include this project in results
            max_depth: Maximum depth to traverse (None for unlimited)

        Returns:
            List of descendant projects, ordered by tree_left (depth-first)

        Example:
            >>> project.get_descendants()
            [<Project(child1)>, <Project(grandchild1)>, <Project(child2)>]
        """
        if not self.tree_left or not self.tree_right:
            return []

        query = self.session.query(Project).filter(
            and_(
                Project.tree_root == self.tree_root,
                Project.tree_left > self.tree_left,
                Project.tree_right < self.tree_right
            )
        ).order_by(Project.tree_left)

        descendants = query.all()

        if max_depth is not None:
            # Calculate depth for each descendant
            my_depth = self.get_depth()
            descendants = [
                d for d in descendants
                if d.get_depth() - my_depth <= max_depth
            ]

        if include_self:
            return [self] + descendants

        return descendants

    def get_children(self) -> List['Project']:
        """
        Get immediate children (one level down) using parent_id.

        This is more efficient than using nested set for direct children.

        Returns:
            List of direct child projects

        Example:
            >>> project.get_children()
            [<Project(child1)>, <Project(child2)>]
        """
        return self.session.query(Project).filter(
            Project.parent_id == self.project_id
        ).all()

    def get_siblings(self, include_self: bool = False) -> List['Project']:
        """
        Get sibling projects (same parent).

        Args:
            include_self: Whether to include this project in results

        Returns:
            List of sibling projects

        Example:
            >>> project.get_siblings()
            [<Project(sibling1)>, <Project(sibling2)>]
        """
        if not self.parent_id:
            return []  # Root nodes have no siblings

        query = self.session.query(Project).filter(
            Project.parent_id == self.parent_id
        )

        if not include_self:
            query = query.filter(Project.project_id != self.project_id)

        return query.all()

    def get_root(self) -> Optional['Project']:
        """
        Get the root project of this tree.

        Returns:
            Root project or None if not part of a tree

        Example:
            >>> project.get_root()
            <Project(root_project)>
        """
        if not self.tree_root:
            return None

        if self.tree_root == self.project_id:
            return self  # This is the root

        return self.session.query(Project).filter(
            Project.project_id == self.tree_root
        ).first()

    def get_depth(self) -> int:
        """
        Calculate the depth of this project in the tree.

        Root nodes have depth 0, their children have depth 1, etc.
        Uses the nested set property that depth = count of ancestors.

        Returns:
            Depth level (0 for root)

        Example:
            >>> root.get_depth()
            0
            >>> child.get_depth()
            1
        """
        if not self.tree_left or not self.tree_right:
            return 0

        return len(self.get_ancestors())

    def get_level(self) -> int:
        """
        Alias for get_depth() - returns tree level (0-based).

        Returns:
            Tree level (0 for root)
        """
        return self.get_depth()

    def is_root(self) -> bool:
        """
        Check if this project is a root node.

        Returns:
            True if this is a root project
        """
        return self.parent_id is None or self.tree_root == self.project_id

    def is_leaf(self) -> bool:
        """
        Check if this project is a leaf node (has no children).

        Uses nested set property: leaf nodes have right = left + 1

        Returns:
            True if this project has no children
        """
        if not self.tree_left or not self.tree_right:
            return True

        return self.tree_right == self.tree_left + 1

    def is_ancestor_of(self, other: 'Project') -> bool:
        """
        Check if this project is an ancestor of another project.

        Args:
            other: Project to check

        Returns:
            True if this project is an ancestor of other

        Example:
            >>> parent.is_ancestor_of(child)
            True
        """
        if not all([self.tree_left, self.tree_right,
                   other.tree_left, other.tree_right]):
            return False

        if self.tree_root != other.tree_root:
            return False

        return (self.tree_left < other.tree_left and
                self.tree_right > other.tree_right)

    def is_descendant_of(self, other: 'Project') -> bool:
        """
        Check if this project is a descendant of another project.

        Args:
            other: Project to check

        Returns:
            True if this project is a descendant of other
        """
        return other.is_ancestor_of(self)

    def get_subtree_size(self) -> int:
        """
        Get the number of descendants (not including self).

        Uses nested set property: size = (right - left - 1) / 2

        Returns:
            Number of descendant nodes
        """
        if not self.tree_left or not self.tree_right:
            return 0

        return (self.tree_right - self.tree_left - 1) // 2

    def get_path(self, separator: str = ' > ') -> str:
        """
        Get the full path from root to this project.

        Args:
            separator: String to join path components

        Returns:
            Path string like "Root > Parent > Child"

        Example:
            >>> project.get_path()
            'RootProject > ParentProject > CurrentProject'
        """
        ancestors = self.get_ancestors(include_self=True)
        return separator.join(p.projcode for p in ancestors)

    def get_breadcrumb_path(self) -> List[Dict[str, any]]:
        """
        Get breadcrumb-style path information.

        Returns:
            List of dicts with project info for each level

        Example:
            >>> project.get_breadcrumb_path()
            [
                {'project_id': 1, 'projcode': 'ROOT', 'title': 'Root'},
                {'project_id': 2, 'projcode': 'CHILD', 'title': 'Child'}
            ]
        """
        ancestors = self.get_ancestors(include_self=True)
        return [
            {
                'project_id': p.project_id,
                'projcode': p.projcode,
                'title': p.title,
                'active': p.active
            }
            for p in ancestors
        ]

    @hybrid_property
    def has_children(self) -> bool:
        """Check if project has children (Python side)."""
        return not self.is_leaf()

    @has_children.expression
    def has_children(cls):
        """Check if project has children (SQL side)."""
        return cls.tree_right > cls.tree_left + 1

    def print_tree(self, indent: str = '  ', _level: int = 0) -> str:
        """
        Generate a text representation of the subtree.

        Args:
            indent: String to use for indentation
            _level: Internal parameter for recursion depth

        Returns:
            Formatted tree string

        Example:
            >>> print(project.print_tree())
            PROJ001: Root Project
              PROJ002: Child 1
                PROJ003: Grandchild
              PROJ004: Child 2
        """
        lines = [f"{indent * _level}{self.projcode}: {self.title}"]

        for child in self.get_children():
            lines.append(child.print_tree(indent, _level + 1))

        return '\n'.join(lines)

    def __str__(self):
        shorttitle = f"{self.title[:50]}..." if len(self.title) > 50 else self.title
        return f"{self.projcode} - {shorttitle}"

    def __repr__(self):
        return f"<Project(id={self.project_id}, projcode='{self.projcode}', title='{self.title[:50]}...')>"


#----------------------------------------------------------------------------
class ProjectNumber(Base):
    """Sequential project numbers."""
    __tablename__ = 'project_number'

    project_number_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'),
                       nullable=False, unique=True)

    project = relationship('Project', back_populates='project_number')


#----------------------------------------------------------------------------
class ProjectDirectory(Base, TimestampMixin, DateRangeMixin):
    """File system directories associated with projects."""
    __tablename__ = 'project_directory'

    __table_args__ = (
        Index('ix_project_directory_project', 'project_id'),
    )

    directory_name = Column(String(255), nullable=False)
    project = relationship('Project', back_populates='directories')
    project_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)


#----------------------------------------------------------------------------
class DefaultProject(Base, TimestampMixin):
    """Default projects for users on resources."""
    __tablename__ = 'default_project'

    __table_args__ = (
        Index('ix_default_project_user', 'user_id'),
        Index('ix_default_project_resource', 'resource_id'),
        Index('ix_default_project_project', 'project_id'),
    )

    default_project_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    user = relationship('User', back_populates='default_projects')
    project = relationship('Project', back_populates='default_projects')
    resource = relationship('Resource', back_populates='default_projects')


# ============================================================================
# Contract Management
# ============================================================================


#-------------------------------------------------------------------------em-
