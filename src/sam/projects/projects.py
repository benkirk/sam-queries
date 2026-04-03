#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-

from ..accounting.accounts import *
from ..accounting.allocations import *
from ..accounting.adjustments import *
from ..resources.resources import *
from ..summaries.comp_summaries import *
from ..summaries.dav_summaries import *
from ..accounting.calculator import calculate_charges, get_charge_models_for_resource

import logging
from typing import Any

from sqlalchemy.orm import joinedload
from sqlalchemy import text
import sqlalchemy.exc as sa_exc

from datetime import timedelta

_logger = logging.getLogger(__name__)

# Lazily detected on first call to either batch charge method.
# True  = DB supports VALUES ROW() CTEs (primary path).
# False = DB does not support VALUES ROW() CTEs (fallback path; warning is logged once).
# None  = not yet tested.
_values_cte_supported: Optional[bool] = None


def _ensure_values_cte_probed(session) -> None:
    """Probe for VALUES ROW() CTE support and cache the result module-wide.

    Called once by both batch_get_subtree_charges and batch_get_account_charges.
    Sets _values_cte_supported to True or False and emits a WARNING on failure so
    the deployment team can treat an unsupported DB version as an actionable issue.
    """
    global _values_cte_supported
    if _values_cte_supported is not None:
        return
    try:
        session.execute(text("SELECT * FROM (VALUES ROW(1)) AS t(n)"))
        _values_cte_supported = True
    except (sa_exc.OperationalError, sa_exc.ProgrammingError):
        try:
            session.rollback()
        except Exception:
            pass
        _values_cte_supported = False
        _logger.warning(
            "Batch charge queries: VALUES ROW() CTEs are not supported by this database "
            "version (requires MariaDB ≥10.3.3 or MySQL ≥8.0.19). "
            "Falling back to less-efficient query strategies. "
            "Results are correct but performance is degraded. "
            "Upgrade the database to enable the optimal CTE path."
        )
#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Project(Base, TimestampMixin, ActiveFlagMixin, SessionMixin, NestedSetMixin):
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

    # NestedSetMixin config
    _ns_pk_col = 'project_id'
    _ns_parent_col = 'parent_id'
    _ns_root_col = 'tree_root'
    _ns_path_attr = 'projcode'

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

    def active_account_users(self, as_of: Optional[datetime] = None) -> List['AccountUser']:
        """Get currently active account users."""
        check_date = as_of or datetime.now()
        return [
            au for account in self.accounts
            for au in account.users
            if au.end_date is None or au.end_date >= check_date
        ]

    @property
    def users(self) -> List['User']:
        """Return deduplicated list of active users."""
        return list({au.user for au in self.active_account_users() if au.user})

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

    def get_user_inaccessible_resources(self, user: 'User') -> Set[str]:
        """
        Determine which resources with active allocations the user cannot access.

        This method compares the resources available to this project (those with active
        allocations) against the resources a specific user can access within this project.

        Args:
            user: User to check for resource access

        Returns:
            Set of resource names user cannot access. Empty set means full access.

        Example:
            >>> project = Project.get_by_projcode(session, 'UCSD0001')
            >>> user = User.get_by_username(session, 'jsmith')
            >>> inaccessible = project.get_user_inaccessible_resources(user)
            >>> if inaccessible:
            ...     print(f"User lacks access to: {', '.join(sorted(inaccessible))}")
        """
        # Get all resources with active allocations
        allocations_by_resource = self.get_all_allocations_by_resource()
        project_resources = set(allocations_by_resource.keys())

        # If no active allocations, no restrictions apply
        if not project_resources:
            return set()

        # Find resources user CAN access in this project
        user_resource_names = set()
        for account_user in user.accounts:  # AccountUser objects
            account = account_user.account

            # Check: Same project + active date range + resource exists
            if (account.project_id == self.project_id and
                account_user.is_active and
                account.resource):
                user_resource_names.add(account.resource.resource_name)

        # Return resources user CANNOT access
        return project_resources - user_resource_names

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
                'parent_allocation_id': query_alloc.parent_allocation_id,
                'is_inheriting': query_alloc.is_inheriting,
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


    @classmethod
    def batch_get_subtree_charges(
        cls,
        session,
        alloc_infos: List[Dict],
        include_adjustments: bool = True,
    ) -> Dict[Any, Dict]:
        """
        Batch version of get_subtree_charges() + get_subtree_adjustments().

        Primary path (VALUES CTE): one SQL query per charge model, with all anchor
        coordinates passed as an inlined VALUES table. The database resolves the MPTT
        range JOIN and returns one charge total per anchor_key.  Requires MariaDB ≥10.3.3
        or MySQL ≥8.0.19 (VALUES ROW() in CTEs).

        Fallback path: if the DB does not support VALUES CTE, the same charge/adjustment
        tables are queried with a resource_id IN filter and grouped by descendant project
        coordinates; attribution back to anchors is done in Python via range containment.
        A WARNING is logged once per process so the deployment team can act on it.

        Parallel to batch_get_account_charges() — both use the same charge model lookup
        (get_charge_models_for_resource) and summary tables; this version follows project
        MPTT tree coordinates while batch_get_account_charges() uses direct account_id.

        Args:
            alloc_infos: List of dicts, each with keys:
                key           — unique identifier (usually allocation_id)
                resource_id   — account.resource_id
                resource_type — e.g. 'HPC', 'DAV', 'DISK', 'ARCHIVE'
                tree_root     — project.tree_root
                tree_left     — project.tree_left
                tree_right    — project.tree_right
                start_date    — allocation start datetime
                end_date      — allocation end datetime (already resolved from check_date)
            include_adjustments: Include ChargeAdjustment amounts in 'adjustment'.

        Returns:
            Dict mapping key -> {'charges_by_type': {charge_key: float}, 'adjustment': float}
        """
        from collections import defaultdict

        result = {info['key']: {'charges_by_type': {}, 'adjustment': 0.0} for info in alloc_infos}

        if not alloc_infos:
            return result

        _ensure_values_cte_probed(session)

        # Group by (resource_type, start_date, end_date) — one DB pass per group per charge model
        date_groups: Dict[tuple, List[Dict]] = defaultdict(list)
        for info in alloc_infos:
            date_groups[(info['resource_type'], info['start_date'], info['end_date'])].append(info)

        for (rt, start_date, end_date), group_infos in date_groups.items():
            models = get_charge_models_for_resource(rt)

            if _values_cte_supported:
                # ----------------------------------------------------------------
                # PRIMARY PATH: VALUES CTE — anchor_key returned directly by the DB
                # ----------------------------------------------------------------
                # Build parameterized VALUES rows: one row per allocation info entry.
                # Each entry is (anchor_key=index, tree_root, tree_left, tree_right, resource_id).
                # Using positional index as anchor_key; mapped back to info['key'] below.
                values_parts = ", ".join(
                    f"ROW(:ak{i}, :tr{i}, :tl{i}, :rr{i}, :ri{i})"
                    for i in range(len(group_infos))
                )
                idx_to_key = {}
                params: Dict[str, Any] = {'start_date': start_date, 'end_date': end_date}
                for i, info in enumerate(group_infos):
                    params[f'ak{i}'] = i
                    params[f'tr{i}'] = info['tree_root']
                    params[f'tl{i}'] = info['tree_left']
                    params[f'rr{i}'] = info['tree_right']
                    params[f'ri{i}'] = info['resource_id']
                    idx_to_key[i] = info['key']

                for charge_key, ModelClass in models.items():
                    sql = text(f"""
                        WITH anchors (anchor_key, tree_root, tree_left, tree_right, resource_id) AS (
                            VALUES {values_parts}
                        )
                        SELECT a.anchor_key, SUM(COALESCE(cs.charges, 0))
                        FROM {ModelClass.__tablename__} cs
                        JOIN account acc ON cs.account_id = acc.account_id
                        JOIN project p   ON acc.project_id = p.project_id
                        JOIN anchors a   ON p.tree_root      =  a.tree_root
                                        AND p.tree_left      >= a.tree_left
                                        AND p.tree_right     <= a.tree_right
                                        AND acc.resource_id  =  a.resource_id
                                        AND cs.activity_date BETWEEN :start_date AND :end_date
                        GROUP BY a.anchor_key
                    """)
                    for anchor_key, amount in session.execute(sql, params).all():
                        if amount:
                            k = idx_to_key[anchor_key]
                            result[k]['charges_by_type'][charge_key] = (
                                result[k]['charges_by_type'].get(charge_key, 0.0) + float(amount)
                            )

                if include_adjustments:
                    adj_sql = text(f"""
                        WITH anchors (anchor_key, tree_root, tree_left, tree_right, resource_id) AS (
                            VALUES {values_parts}
                        )
                        SELECT a.anchor_key, SUM(COALESCE(ca.amount, 0))
                        FROM charge_adjustment ca
                        JOIN account acc ON ca.account_id  = acc.account_id
                        JOIN project p   ON acc.project_id = p.project_id
                        JOIN anchors a   ON p.tree_root      =  a.tree_root
                                        AND p.tree_left      >= a.tree_left
                                        AND p.tree_right     <= a.tree_right
                                        AND acc.resource_id  =  a.resource_id
                                        AND ca.adjustment_date BETWEEN :start_date AND :end_date
                        GROUP BY a.anchor_key
                    """)
                    for anchor_key, amount in session.execute(adj_sql, params).all():
                        if amount:
                            result[idx_to_key[anchor_key]]['adjustment'] += float(amount)

            else:
                # ----------------------------------------------------------------
                # FALLBACK PATH: resource_id IN filter + Python-side MPTT attribution
                # ----------------------------------------------------------------
                resource_ids = list({info['resource_id'] for info in group_infos})

                # Build anchor-coord → list-of-keys map to handle duplicate anchor coords
                anchor_to_keys: Dict[tuple, List] = defaultdict(list)
                for info in group_infos:
                    coord = (info['tree_root'], info['tree_left'], info['tree_right'], info['resource_id'])
                    anchor_to_keys[coord].append(info['key'])

                for charge_key, ModelClass in models.items():
                    rows = session.query(
                        Project.tree_root,
                        Project.tree_left,
                        Project.tree_right,
                        Account.resource_id,
                        func.coalesce(func.sum(ModelClass.charges), 0),
                    ).join(Account, ModelClass.account_id == Account.account_id)\
                     .join(Project, Account.project_id == Project.project_id)\
                     .filter(
                         Account.resource_id.in_(resource_ids),
                         ModelClass.activity_date >= start_date,
                         ModelClass.activity_date <= end_date,
                     )\
                     .group_by(Project.tree_root, Project.tree_left, Project.tree_right, Account.resource_id)\
                     .all()

                    desc_charges = [(r[0], r[1], r[2], r[3], float(r[4])) for r in rows if r[4]]

                    for d_root, d_left, d_right, d_res, amount in desc_charges:
                        for (a_root, a_left, a_right, a_res), keys in anchor_to_keys.items():
                            if (d_root == a_root and d_res == a_res
                                    and d_left >= a_left and d_right <= a_right):
                                for k in keys:
                                    result[k]['charges_by_type'][charge_key] = (
                                        result[k]['charges_by_type'].get(charge_key, 0.0) + amount
                                    )

                if include_adjustments:
                    adj_rows = session.query(
                        Project.tree_root,
                        Project.tree_left,
                        Project.tree_right,
                        Account.resource_id,
                        func.coalesce(func.sum(ChargeAdjustment.amount), 0),
                    ).join(Account, ChargeAdjustment.account_id == Account.account_id)\
                     .join(Project, Account.project_id == Project.project_id)\
                     .filter(
                         Account.resource_id.in_(resource_ids),
                         ChargeAdjustment.adjustment_date >= start_date,
                         ChargeAdjustment.adjustment_date <= end_date,
                     )\
                     .group_by(Project.tree_root, Project.tree_left, Project.tree_right, Account.resource_id)\
                     .all()

                    desc_adjs = [(r[0], r[1], r[2], r[3], float(r[4])) for r in adj_rows if r[4]]

                    for d_root, d_left, d_right, d_res, amount in desc_adjs:
                        for (a_root, a_left, a_right, a_res), keys in anchor_to_keys.items():
                            if (d_root == a_root and d_res == a_res
                                    and d_left >= a_left and d_right <= a_right):
                                for k in keys:
                                    result[k]['adjustment'] += amount

        return result


    @classmethod
    def batch_get_account_charges(
        cls,
        session,
        alloc_infos: List[Dict],
        include_adjustments: bool = True,
    ) -> Dict[Any, Dict]:
        """
        Batch version of get_charges_by_resource_type() + get_adjustments().

        Primary path (VALUES CTE): groups by resource_type only, issuing one query per
        charge model with all account_ids and their individual date ranges as an inlined
        VALUES table. The per-anchor date range is enforced in the JOIN ON clause, so
        allocations with diverse date ranges are handled in a single pass.

        Fallback path: groups by (resource_type, start_date, end_date) and issues one
        query per charge model per date group (correct but more queries for diverse ranges).

        Parallel to batch_get_subtree_charges() — both use the same charge model lookup
        (get_charge_models_for_resource) and summary tables; this version filters by
        direct account_id while batch_get_subtree_charges() uses MPTT tree coordinates.

        Args:
            alloc_infos: List of dicts, each with keys:
                key           — unique identifier (usually allocation_id)
                account_id    — direct account_id filter
                resource_type — e.g. 'HPC', 'DAV', 'DISK', 'ARCHIVE'
                start_date    — allocation start datetime
                end_date      — allocation end datetime (already resolved from check_date)
            include_adjustments: Include ChargeAdjustment amounts in 'adjustment'.

        Returns:
            Dict mapping key -> {'charges_by_type': {charge_key: float}, 'adjustment': float}
        """
        from collections import defaultdict

        result = {info['key']: {'charges_by_type': {}, 'adjustment': 0.0} for info in alloc_infos}

        if not alloc_infos:
            return result

        _ensure_values_cte_probed(session)

        if _values_cte_supported:
            # ----------------------------------------------------------------
            # PRIMARY PATH: VALUES CTE — group by resource_type only.
            # All accounts and their individual date ranges are inlined as a
            # VALUES table; the JOIN ON clause enforces per-anchor date filtering.
            # Reduces queries to: N_resource_types × N_charge_models + adjustments.
            # ----------------------------------------------------------------
            rt_groups: Dict[str, List[Dict]] = defaultdict(list)
            for info in alloc_infos:
                rt_groups[info['resource_type']].append(info)

            for rt, group_infos in rt_groups.items():
                values_parts = ", ".join(
                    f"ROW(:ak{i}, :acct{i}, :sd{i}, :ed{i})"
                    for i in range(len(group_infos))
                )
                idx_to_key: Dict[int, Any] = {}
                params: Dict[str, Any] = {}
                for i, info in enumerate(group_infos):
                    params[f'ak{i}']   = i
                    params[f'acct{i}'] = info['account_id']
                    params[f'sd{i}']   = info['start_date']
                    params[f'ed{i}']   = info['end_date']
                    idx_to_key[i]      = info['key']

                models = get_charge_models_for_resource(rt)

                for charge_key, ModelClass in models.items():
                    sql = text(f"""
                        WITH anchors (anchor_key, account_id, start_date, end_date) AS (
                            VALUES {values_parts}
                        )
                        SELECT a.anchor_key, SUM(COALESCE(cs.charges, 0))
                        FROM {ModelClass.__tablename__} cs
                        JOIN anchors a ON cs.account_id       =  a.account_id
                                       AND cs.activity_date BETWEEN a.start_date AND a.end_date
                        GROUP BY a.anchor_key
                    """)
                    for anchor_key, amount in session.execute(sql, params).all():
                        if amount:
                            k = idx_to_key[anchor_key]
                            result[k]['charges_by_type'][charge_key] = (
                                result[k]['charges_by_type'].get(charge_key, 0.0) + float(amount)
                            )

                if include_adjustments:
                    adj_sql = text(f"""
                        WITH anchors (anchor_key, account_id, start_date, end_date) AS (
                            VALUES {values_parts}
                        )
                        SELECT a.anchor_key, SUM(COALESCE(ca.amount, 0))
                        FROM charge_adjustment ca
                        JOIN anchors a ON ca.account_id        =  a.account_id
                                       AND ca.adjustment_date BETWEEN a.start_date AND a.end_date
                        GROUP BY a.anchor_key
                    """)
                    for anchor_key, amount in session.execute(adj_sql, params).all():
                        if amount:
                            result[idx_to_key[anchor_key]]['adjustment'] += float(amount)

        else:
            # ----------------------------------------------------------------
            # FALLBACK PATH: date-group bucketing (correct but more queries)
            # ----------------------------------------------------------------
            date_groups: Dict[tuple, List[Dict]] = defaultdict(list)
            for info in alloc_infos:
                date_groups[(info['resource_type'], info['start_date'], info['end_date'])].append(info)

            for (rt, start_date, end_date), group_infos in date_groups.items():
                account_ids = list({info['account_id'] for info in group_infos})

                acct_to_keys: Dict[int, List] = defaultdict(list)
                for info in group_infos:
                    acct_to_keys[info['account_id']].append(info['key'])

                models = get_charge_models_for_resource(rt)

                for charge_key, ModelClass in models.items():
                    rows = session.query(
                        ModelClass.account_id,
                        func.coalesce(func.sum(ModelClass.charges), 0),
                    ).filter(
                        ModelClass.account_id.in_(account_ids),
                        ModelClass.activity_date >= start_date,
                        ModelClass.activity_date <= end_date,
                    )\
                     .group_by(ModelClass.account_id)\
                     .all()

                    for account_id, amount in rows:
                        if amount:
                            for k in acct_to_keys.get(account_id, []):
                                result[k]['charges_by_type'][charge_key] = (
                                    result[k]['charges_by_type'].get(charge_key, 0.0) + float(amount)
                                )

                if include_adjustments:
                    adj_rows = session.query(
                        ChargeAdjustment.account_id,
                        func.coalesce(func.sum(ChargeAdjustment.amount), 0),
                    ).filter(
                        ChargeAdjustment.account_id.in_(account_ids),
                        ChargeAdjustment.adjustment_date >= start_date,
                        ChargeAdjustment.adjustment_date <= end_date,
                    )\
                     .group_by(ChargeAdjustment.account_id)\
                     .all()

                    for account_id, amount in adj_rows:
                        if amount:
                            for k in acct_to_keys.get(account_id, []):
                                result[k]['adjustment'] += float(amount)

        return result


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

    def get_root(self) -> Optional['Project']:
        """
        Get the root project of this tree (fast FK-based lookup via tree_root).

        Returns:
            Root project or None if not part of a tree
        """
        if not self.tree_root:
            return None

        if self.tree_root == self.project_id:
            return self  # This is the root

        return self.session.query(Project).filter(
            Project.project_id == self.tree_root
        ).first()

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

    def __str__(self):
        return f"ProjectNumber {self.project_number_id}: project={self.project_id}"

    def __repr__(self):
        return f"<ProjectNumber(id={self.project_number_id}, project_id={self.project_id})>"


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

    def __str__(self):
        return f"{self.directory_name} (project {self.project_id})"

    def __repr__(self):
        return f"<ProjectDirectory(id={self.project_directory_id}, dir='{self.directory_name}', project_id={self.project_id})>"


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

    def __str__(self):
        projcode = self.project.projcode if self.project else self.project_id
        return f"DefaultProject: user={self.user_id} -> {projcode}"

    def __repr__(self):
        return f"<DefaultProject(id={self.default_project_id}, user_id={self.user_id}, project_id={self.project_id}, resource_id={self.resource_id})>"


# ============================================================================
# Contract Management
# ============================================================================


#-------------------------------------------------------------------------em-
