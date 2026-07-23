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
from ..enums import ResourceTypeName

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
        Index('project_projcode_uk', 'projcode', unique=True),
        Index('project_lead_user_fk', 'project_lead_user_id'),
        Index('project_admin_user_fk', 'project_admin_user_id'),
        Index('project_project_fk', 'parent_id'),
        Index('project_allocation_type_fk', 'allocation_type_id'),
        Index('project_aoi_fk', 'area_of_interest_id'),
        Index('project_root_fk', 'tree_root'),
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
    projcode = Column(String(30), nullable=False, default='')
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

    @classmethod
    def create(cls, session, *, projcode: str, title: str, project_lead_user_id: int,
               area_of_interest_id: int, abstract: Optional[str] = None,
               project_admin_user_id: Optional[int] = None,
               allocation_type_id: Optional[int] = None,
               parent_id: Optional[int] = None,
               charging_exempt: bool = False,
               unix_gid: Optional[int] = None,
               ext_alias: Optional[str] = None) -> 'Project':
        """Create a new project and place it correctly in the nested-set tree.

        All keyword arguments are required or keyword-only to prevent positional
        mistakes.  ``projcode`` is uppercased and stripped automatically.

        The new project inherits ``tree_root`` from its parent (if provided) and
        receives correct ``tree_left`` / ``tree_right`` coordinates via
        ``_ns_place_in_tree``.  Root projects (no parent) start their own
        single-node tree.

        Args:
            session: SQLAlchemy session.
            projcode: Unique project code (e.g. ``'UCSD0001'``).
            title: Human-readable project title.
            project_lead_user_id: FK to the project lead User.
            area_of_interest_id: FK to AreaOfInterest (required).
            abstract: Optional longer description.
            project_admin_user_id: Optional FK to project admin User.
            allocation_type_id: Optional FK to AllocationType.
            parent_id: Optional FK to parent Project (for sub-projects).
            charging_exempt: If True, charges are not assessed. Default False.
            unix_gid: Optional Unix group ID.
            ext_alias: Optional external alias string.

        Returns:
            The newly created and flushed Project instance.

        Example::

            project = Project.create(
                session,
                projcode='UCSD0042',
                title='Mesoscale Climate Dynamics',
                project_lead_user_id=lead_user.user_id,
                area_of_interest_id=aoi.area_of_interest_id,
            )
        """
        parent = session.get(cls, parent_id) if parent_id else None

        project = cls(
            projcode=projcode.upper().strip(),
            title=title.strip(),
            abstract=abstract.strip() if abstract else None,
            project_lead_user_id=project_lead_user_id,
            project_admin_user_id=project_admin_user_id,
            area_of_interest_id=area_of_interest_id,
            allocation_type_id=allocation_type_id,
            parent_id=parent_id,
            charging_exempt=charging_exempt,
            unix_gid=unix_gid,
            ext_alias=ext_alias.strip() if ext_alias else None,
        )
        session.add(project)
        session.flush()  # assigns project_id before tree placement

        # Set tree coordinates (also sets tree_root and shifts siblings)
        project._ns_place_in_tree(session, parent)

        # Create sequential project number (mirrors legacy SAM createProjectNumber())
        pn = ProjectNumber(project_id=project.project_id)
        session.add(pn)
        session.flush()

        return project

    def update(
        self,
        *,
        title: Optional[str] = None,
        abstract: Optional[str] = None,
        area_of_interest_id: Optional[int] = None,
        allocation_type_id: Optional[int] = None,
        charging_exempt: Optional[bool] = None,
        project_lead_user_id: Optional[int] = None,
        project_admin_user_id: Optional[int] = None,
        unix_gid: Optional[int] = None,
        ext_alias: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'Project':
        """Update mutable project fields and flush.

        Only keyword arguments explicitly provided (non-None) are written.
        ``projcode`` and ``parent_id`` are intentionally excluded — projcode
        is immutable after creation, and tree restructuring is a separate op.

        Uses ``SessionMixin.session`` (``Session.object_session(self)``) for
        the flush, so no session argument is needed.

        Args:
            title:                 Human-readable project title.
            abstract:              Longer project description (pass ``''`` to clear).
            area_of_interest_id:   FK to AreaOfInterest.
            allocation_type_id:    FK to AllocationType.
            charging_exempt:       If True, charges are not assessed.
            project_lead_user_id:  FK to lead User.
            project_admin_user_id: FK to admin User.
            unix_gid:              Unix group ID.
            ext_alias:             External alias string (pass ``''`` to clear).
            active:                Active flag.

        Returns:
            self (for chaining).
        """
        if title is not None:
            self.title = title.strip()
        if abstract is not None:
            self.abstract = abstract.strip() or None
        if area_of_interest_id is not None:
            self.area_of_interest_id = area_of_interest_id
        if allocation_type_id is not None:
            self.allocation_type_id = allocation_type_id
        if charging_exempt is not None:
            self.charging_exempt = charging_exempt
        if project_lead_user_id is not None:
            self.project_lead_user_id = project_lead_user_id
        if project_admin_user_id is not None:
            self.project_admin_user_id = project_admin_user_id
        if unix_gid is not None:
            self.unix_gid = unix_gid
        if ext_alias is not None:
            self.ext_alias = ext_alias.strip() or None
        if active is not None:
            self.active = active
        self.session.flush()
        return self

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
    def facility_name(self) -> Optional[str]:
        """The facility this project belongs to, derived via the
        ``allocation_type → panel → facility`` chain.

        Returns ``None`` for orphan projects — those with no
        ``allocation_type`` assigned, or whose allocation_type chain
        is broken. The RBAC facility-scope layer treats ``None`` as
        "unscoped users cannot act here" (only system-permission
        holders may)."""
        at = self.allocation_type
        if at is None:
            return None
        panel = at.panel
        if panel is None:
            return None
        facility = panel.facility
        if facility is None:
            return None
        return facility.facility_name

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

    def get_members_access_status(self, active_only: bool = True) -> Dict[str, Any]:
        """Per-member resource-access status for this project.

        The single source of truth shared by the member-list warning
        indicator, the CLI (``build_project_users``), and the operator
        access grid (``_build_access_grid_context``) — so those surfaces
        can never drift in how they classify partial access.

        Columns are the resource "denominator":

        - ``active_only=True`` (default) → resources with a currently-active
          allocation (via :meth:`get_all_allocations_by_resource`). Used by
          the member list and CLI.
        - ``active_only=False`` → every non-deleted account's resource, so
          lapsed resources are shown too (the grid's "show all" mode).

        A member has *partial* access when they hold an active
        ``AccountUser`` on some but not all columns, *none* when they hold
        none, *full* otherwise. The project lead is flagged (``is_lead``) so
        callers can apply the "lead always has access" business rule.

        Returns a dict::

            {
              'columns': [
                {account_id, resource_id, resource_name, has_active_alloc}, ...
              ],                                          # sorted by resource_name
              'members': [
                {
                  'user': User, 'is_lead': bool,
                  'has': set[str],                        # resource_names with access
                  'missing': [ {account_id, resource_id, resource_name}, ... ],
                  'status': 'full' | 'partial' | 'none',
                  'cells': [ {column, checked}, ... ],    # aligned with 'columns'
                }, ...
              ],
            }
        """
        active_by_resource = self.get_all_allocations_by_resource()
        active_resource_names = set(active_by_resource.keys())

        columns = []
        if active_only:
            for resource_name, allocation in active_by_resource.items():
                account = allocation.account
                columns.append({
                    'account_id': account.account_id,
                    'resource_id': account.resource_id,
                    'resource_name': resource_name,
                    'has_active_alloc': True,
                })
        else:
            for account in self.accounts:
                if not account.is_active or not account.resource:
                    continue
                resource_name = account.resource.resource_name
                columns.append({
                    'account_id': account.account_id,
                    'resource_id': account.resource_id,
                    'resource_name': resource_name,
                    'has_active_alloc': resource_name in active_resource_names,
                })
        columns.sort(key=lambda c: (c['resource_name'] or '').lower())

        # One membership query for the whole grid — not per-cell lookups.
        account_ids = [c['account_id'] for c in columns]
        active_links = set()
        if account_ids:
            rows = self.session.query(
                AccountUser.user_id, AccountUser.account_id
            ).filter(
                AccountUser.account_id.in_(account_ids),
                AccountUser.is_active,
            ).all()
            active_links = {(uid, aid) for uid, aid in rows}

        lead_user_id = self.project_lead_user_id
        members = sorted(
            self.users,
            key=lambda u: (u.display_name or u.username or '').lower(),
        )

        member_rows = []
        for user in members:
            cells = []
            has = set()
            missing = []
            for col in columns:
                checked = (user.user_id, col['account_id']) in active_links
                cells.append({'column': col, 'checked': checked})
                if checked:
                    has.add(col['resource_name'])
                else:
                    missing.append({
                        'account_id': col['account_id'],
                        'resource_id': col['resource_id'],
                        'resource_name': col['resource_name'],
                    })
            if not missing:
                status = 'full'
            elif not has:
                status = 'none'
            else:
                status = 'partial'
            member_rows.append({
                'user': user,
                'is_lead': user.user_id == lead_user_id,
                'has': has,
                'missing': missing,
                'status': status,
                'cells': cells,
            })

        return {'columns': columns, 'members': member_rows}

    def get_user_inaccessible_resources(self, user: 'User') -> Set[str]:
        """
        Determine which resources with active allocations the user cannot access.

        Thin per-user wrapper over :meth:`get_members_access_status` (the
        shared detector) so this convenience method and the grid/CLI cannot
        disagree. Returns the set of resource names the user cannot access;
        an empty set means full access.

        Example:
            >>> project = Project.get_by_projcode(session, 'UCSD0001')
            >>> user = User.get_by_username(session, 'jsmith')
            >>> inaccessible = project.get_user_inaccessible_resources(user)
            >>> if inaccessible:
            ...     print(f"User lacks access to: {', '.join(sorted(inaccessible))}")
        """
        status = self.get_members_access_status(active_only=True)

        all_resources = {c['resource_name'] for c in status['columns']}
        if not all_resources:
            return set()

        for row in status['members']:
            if row['user'].user_id == user.user_id:
                return {m['resource_name'] for m in row['missing']}

        # Caller passed a user who is not a listed project member — compute
        # directly from their own AccountUser rows against the same column set.
        accessible = {
            au.account.resource.resource_name
            for au in user.accounts
            if (au.account.project_id == self.project_id and
                au.is_active and au.account.resource)
        }
        return all_resources - accessible

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
                                      hierarchical: bool = True,
                                      active_at: Optional[datetime] = None) -> Dict[str, Dict[str, any]]:
        """
        Calculate allocation usage and remaining balance across all resource types.

        Args:
            resource_name: Optional filter for specific resource (e.g., 'Derecho', 'GLADE')
            include_adjustments: Whether to include manual charge adjustments
            hierarchical: If True, aggregate usage from this project and all descendants (sub-projects).
                          If False, only count usage for this specific project.
            active_at: Reference datetime for determining which allocation is "active".
                       Defaults to now.

        Returns:
            Dict mapping resource_name to usage details.
        """
        now = active_at or datetime.now()
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
                    # Apply date threshold: only query 'most_recent_alloc'
                    # if it has expired within the past 90 days.
                    # An open-ended allocation (end_date=None) never expires —
                    # treat it as always within threshold.
                    end = most_recent_alloc.end_date
                    if end is None or (now - end) <= timedelta(days=90):
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
            # `effective_used` here represents this project's subtree
            # contribution to a (possibly shared) allocation pool. When the
            # allocation is inheriting, the authoritative pool consumption
            # lives at the root allocation's project subtree — compute it
            # so the UI shows truth instead of just self-share.
            self_used = effective_used
            tree_used = effective_used
            root_projcode = None
            if query_alloc.is_inheriting:
                root_alloc = query_alloc.root
                root_account = root_alloc.account
                root_project = root_account.project if root_account else None
                if root_project is not None and root_project.tree_root \
                        and root_project.tree_left and root_project.tree_right:
                    root_charges = root_project.get_subtree_charges(
                        account.resource_id, resource_type, start_date, end_date)
                    root_total = sum(root_charges.values())
                    if include_adjustments:
                        root_total += root_project.get_subtree_adjustments(
                            account.resource_id, start_date, end_date)
                    tree_used = root_total
                    root_projcode = root_project.projcode

            effective_used = tree_used
            remaining = allocated - effective_used
            percent_used = (effective_used / allocated * 100) if allocated > 0 else 0
            self_percent_used = (self_used / allocated * 100) if allocated > 0 else 0

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

            # When the allocation is shared (inheriting), surface this
            # project's own contribution and the root projcode so the UI
            # can render a two-tone bar / inline annotation. Non-inheriting
            # allocations keep the original shape.
            if query_alloc.is_inheriting:
                result['self_used'] = self_used
                result['self_percent_used'] = self_percent_used
                result['root_projcode'] = root_projcode

            if include_adjustments:
                result['adjustments'] = adjustments

            if total_jobs is not None:
                result['total_jobs'] = total_jobs
                result['total_core_hours'] = total_core_hours

            results[resource] = result

        # Disk resources need a different "% used" — point-in-time TiB
        # capacity (snapshot bytes / allocated TiB), not cumulative
        # TiB-yr burn. Apply the override in one bulk pass so the helper
        # is the single source of truth across CLI, API, admin tree
        # view, and the single-project dashboard path. `charges_by_type`
        # stays TiB-yr for billing-side consumers.
        disk_names = [name for name, r in results.items()
                      if r.get('resource_type') == 'DISK']
        if disk_names:
            from sam.queries.disk_usage import bulk_get_subtree_disk_capacity
            caps = bulk_get_subtree_disk_capacity(
                self.session, [(self, name) for name in disk_names],
            )
            for name in disk_names:
                cap = caps.get((self.project_id, name))
                if cap is None:
                    continue
                allocated = results[name].get('allocated', 0.0) or 0.0
                used_tib = cap['used_tib']
                pct = (used_tib / allocated * 100) if allocated > 0 else 0.0
                results[name]['used'] = used_tib
                results[name]['remaining'] = allocated - used_tib
                results[name]['percent_used'] = pct
                results[name]['activity_date'] = cap['activity_date']

        return results


    def current_disk_usage(self, resource_name: Optional[str] = None) -> Dict[str, Any]:
        """Return the latest disk-snapshot occupancy for each disk account on this project.

        Result is keyed by resource name and shaped as::

            {
              "Campaign_Store": {
                "activity_date": date(2026, 4, 18),
                "bytes": 241_601_257_783_296,
                "current_used_tib": 219.71...,
                "terabyte_years": 4.215...,
                "number_of_files": 10_455_444,
              },
              ...
            }

        Distinct from ``get_detailed_allocation_usage()`` (which sums
        cumulative TiB-years over an allocation window). Use this for
        "how full is this project right now?" UI questions; use the
        cumulative method for billing.

        ``resource_name`` filters to a single disk resource if given.
        """
        # Gather disk-account candidates once, then a single bulk
        # snapshot query covers them all (matches single-account
        # semantics in Account.current_disk_usage but with fixed query
        # count instead of N+1).
        candidates = []
        for account in self.accounts:
            if account.deleted:
                continue
            if not account.resource:
                continue
            res_name = account.resource.resource_name
            if resource_name and res_name != resource_name:
                continue
            rt = account.resource.resource_type
            if rt and rt.resource_type != 'DISK':
                continue
            candidates.append((account, res_name))

        if not candidates:
            return {}

        from sam.queries.disk_usage import bulk_current_disk_usage
        snapshots = bulk_current_disk_usage(
            self.session, [a.account_id for a, _ in candidates],
        )

        results: Dict[str, Any] = {}
        for account, res_name in candidates:
            usage = snapshots.get(account.account_id)
            if usage is None:
                continue
            results[res_name] = {
                'activity_date': usage.activity_date,
                'bytes': usage.bytes,
                'current_used_tib': usage.used_tib,
                'terabyte_years': usage.terabyte_years,
                'number_of_files': usage.number_of_files,
            }
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
        if not ResourceTypeName.is_compute(resource_type):
            return None, None

        # Use appropriate summary table
        SummaryClass = CompChargeSummary if resource_type == ResourceTypeName.HPC else DavChargeSummary

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
        if not ResourceTypeName.is_compute(resource_type):
            return None, None

        SummaryClass = CompChargeSummary if resource_type == ResourceTypeName.HPC else DavChargeSummary

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

    __table_args__ = (
        Index('project_number_project_id_uk', 'project_id', unique=True),
        Index('project_number_project_fk', 'project_id'),
    )

    project_number_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'),
                       nullable=False)

    project = relationship('Project', back_populates='project_number')

    def __str__(self):
        return f"ProjectNumber {self.project_number_id}: project={self.project_id}"

    def __repr__(self):
        return f"<ProjectNumber(id={self.project_number_id}, project_id={self.project_id})>"


#----------------------------------------------------------------------------
class ProjectDirectory(Base, TimestampMixin, DateRangeMixin, SessionMixin):
    """File system directories associated with projects."""
    __tablename__ = 'project_directory'

    __table_args__ = (
        Index('project_directory_project_fk', 'project_id'),
    )

    directory_name = Column(String(255), nullable=False)
    project = relationship('Project', back_populates='directories')
    project_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    disk_charge_summaries = relationship('DiskChargeSummary',
                                         back_populates='project_directory')

    @classmethod
    def create(cls, session, *, project_id: int, directory_name: str,
               start_date=None) -> 'ProjectDirectory':
        """Associate a filesystem directory with a project.

        Does NOT commit; caller must wrap in management_transaction().
        """
        from datetime import datetime
        if not directory_name or not directory_name.strip():
            raise ValueError("directory_name is required")
        obj = cls(
            project_id=project_id,
            directory_name=directory_name.strip(),
            start_date=start_date or datetime.now(),
        )
        session.add(obj)
        session.flush()
        return obj

    def update(self, *, directory_name=None, project_id=None) -> 'ProjectDirectory':
        """Update this directory's name and/or linked project.

        Does NOT commit; caller must wrap in management_transaction().
        """
        if directory_name is not None:
            cleaned = directory_name.strip()
            if not cleaned:
                raise ValueError("directory_name cannot be blank")
            self.directory_name = cleaned
        if project_id is not None:
            self.project_id = project_id
        self.session.flush()
        return self

    def deactivate(self) -> 'ProjectDirectory':
        """End this directory association by setting end_date to now.

        Does NOT commit; caller must wrap in management_transaction().
        """
        from datetime import datetime
        self.end_date = datetime.now()
        self.session.flush()
        return self

    def __str__(self):
        return f"{self.directory_name} (project {self.project_id})"

    def __repr__(self):
        return f"<ProjectDirectory(id={self.project_directory_id}, dir='{self.directory_name}', project_id={self.project_id})>"


#----------------------------------------------------------------------------
class DefaultProject(Base, TimestampMixin):
    """Default projects for users on resources."""
    __tablename__ = 'default_project'

    __table_args__ = (
        Index('idx_default_project', 'user_id'),
        Index('idx_default_project_1', 'project_id'),
        Index('idx_default_project_2', 'resource_id'),
        Index('idx_default_project_user_resource',
              'user_id', 'resource_id', unique=True),
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


# ============================================================================
# Project Code Generation
# ============================================================================

class ProjcodeExhaustedError(Exception):
    """No collision-free projcode found within the attempt budget."""


def formulate_projcode(facility_code: str, mnemonic_code: str, number: int) -> str:
    """Assemble a projcode from its parts — legacy ``%s%s%04d`` format.

    e.g. ``('U', 'ALB', 57) → 'UALB0057'``, ``('S', 'CSG', 9) → 'SCSG0009'``.
    """
    return f"{facility_code}{mnemonic_code}{number:04d}"


def projcode_collision(session, code: str):
    """Return a short human-readable reason if ``code`` is taken, else None.

    A candidate collides when it matches an existing ``Project.projcode``
    or an ``adhoc_group.group_name`` (projcodes become Unix group names —
    the namespaces must not overlap; legacy `GroupSensitiveProjcodeGenerator`
    enforced the latter). Comparisons are case-insensitive: a projcode and
    its Unix group differ only by case convention.
    """
    from ..core.groups import AdhocGroup

    existing = (
        session.query(Project)
        .filter(func.upper(Project.projcode) == code.upper())
        .first()
    )
    if existing:
        return f'project "{existing.projcode}" — {existing.title or "untitled"}'

    group = (
        session.query(AdhocGroup)
        .filter(func.upper(AdhocGroup.group_name) == code.upper())
        .first()
    )
    if group:
        return f'unix group "{group.group_name}" (gid {group.unix_gid})'

    return None


def next_projcode(session, facility_id: int, mnemonic_code_id: int,
                  *, allocate: bool = False, max_attempts: int = 100) -> str:
    """Next available projcode for a facility + mnemonic pair.

    Faithful port of legacy SAM's ``GroupSensitiveProjcodeGenerator`` +
    ``Facility.getNextProjectCodeDigits()``:

    - ``project_code.digits`` is a **per-(facility, mnemonic) counter of the
      last sequence number issued** — NOT a zero-pad width. The rendered
      code is always ``<facility.code><mnemonic.code><NNNN>`` with the
      number zero-padded to 4 (``'%s%s%04d'``), e.g. ``UALB0057``.
    - A missing ``ProjectCode`` row is created on demand starting at 1.
    - Candidates colliding with an existing projcode or adhoc_group name
      are skipped (legacy retried, burning counter values).

    Args:
        session: SQLAlchemy session.
        facility_id: FK to a Facility row.
        mnemonic_code_id: FK to a MnemonicCode row.
        allocate: When True, persist the consumed counter value back to
            ``project_code.digits`` (creating the row if needed) and flush —
            call inside the project-creation transaction. When False
            (default) the computation is a side-effect-free preview.
        max_attempts: Collision-retry budget (legacy default 100).

    Returns:
        Next projcode string, e.g. ``'UALB0058'``.

    Raises:
        ValueError: If the facility or mnemonic row doesn't exist, or the
            facility has no single-letter projcode prefix (``facility.code``).
        ProjcodeExhaustedError: If no free code is found within
            ``max_attempts``.
    """
    from ..core.organizations import MnemonicCode
    from ..resources.facilities import Facility, ProjectCode

    facility = session.get(Facility, facility_id)
    if not facility:
        raise ValueError(f"No Facility with id={facility_id}")
    if not facility.code:
        raise ValueError(
            f"Facility {facility.facility_name!r} has no projcode prefix letter"
        )
    mnemonic = session.get(MnemonicCode, mnemonic_code_id)
    if not mnemonic:
        raise ValueError(f"No MnemonicCode with id={mnemonic_code_id}")

    pc = session.get(ProjectCode, (facility_id, mnemonic_code_id))
    last_issued = pc.digits if pc else 0

    number = last_issued
    for _ in range(max_attempts):
        number += 1
        code = formulate_projcode(facility.code, mnemonic.code, number)
        if not projcode_collision(session, code):
            break
    else:
        raise ProjcodeExhaustedError(
            f"Could not generate projcode for facility {facility.code!r} "
            f"and mnemonic {mnemonic.code!r} within {max_attempts} attempts"
        )

    if allocate:
        if pc is None:
            pc = ProjectCode(facility_id=facility_id,
                             mnemonic_code_id=mnemonic_code_id,
                             digits=number)
            session.add(pc)
        else:
            pc.digits = number
        session.flush()

    return code


#-------------------------------------------------------------------------em-
