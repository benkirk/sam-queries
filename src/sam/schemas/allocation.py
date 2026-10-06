"""
Allocation and Account schemas for API serialization.

Provides schemas for Allocation and Account models, including allocation usage
calculations matching the sam_search.py CLI output.

Usage:
    from sam.schemas import AllocationSchema, AllocationWithUsageSchema, AccountSchema

    # Basic allocation
    alloc_data = AllocationSchema().dump(allocation)

    # Allocation with usage (shows used, remaining, percent_used)
    usage_data = AllocationWithUsageSchema().dump(allocation, context={'account': account})

    # Account with allocations
    account_data = AccountSchema().dump(account)
"""

from marshmallow import fields
from datetime import datetime
from . import BaseSchema
from .resource import ResourceSummarySchema
from .project import ProjectSummarySchema
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.summaries.disk_summaries import BYTES_PER_TIB
from sam.accounting.calculator import anchored_charges, usage_anchor


def _keep_fixed_keys(charges: dict, activity_type) -> dict:
    """DISK/ARCHIVE always carry their key, even at zero (the read-model row does too)."""
    if activity_type in ('DISK', 'ARCHIVE'):
        charges.setdefault(activity_type.lower(), 0.0)
    return charges


class AccountSummarySchema(BaseSchema):
    """
    Minimal account schema for nested references.
    """
    class Meta(BaseSchema.Meta):
        model = Account
        fields = ('account_id', 'project', 'resource')

    # Nested minimal references
    project = fields.Nested(ProjectSummarySchema)
    resource = fields.Nested(ResourceSummarySchema)


class AllocationSchema(BaseSchema):
    """
    Basic allocation schema without usage calculations.

    Includes allocation amount, dates, and active status.
    """
    class Meta(BaseSchema.Meta):
        model = Allocation
        fields = (
            'allocation_id',
            'account_id',
            'amount',
            'description',
            'start_date',
            'end_date',
            'is_active',
            'deleted',
            'parent_allocation_id',
            'creation_time',
            'modified_time',
        )

    # Computed is_active field
    is_active = fields.Method('get_is_active')

    def get_is_active(self, obj):
        """Get computed is_active status."""
        return obj.is_active_at(datetime.now())


class AllocationWithUsageSchema(AllocationSchema):
    """
    **KEY SCHEMA** - Allocation with usage calculations.

    This schema extends AllocationSchema to include:
    - used: Total charges consumed
    - remaining: Allocation amount - used
    - percent_used: (used / amount) * 100
    - charges_by_type: Breakdown by charge type (comp, dav, disk, archive)
    - adjustments: Manual charge adjustments (if include_adjustments=True)
    - resource: Nested resource details

    This matches the output from sam_search.py --verbose.

    Context parameters:
        - account: Account object (required)
        - include_adjustments: Include manual adjustments (default: True)
        - session: SQLAlchemy session (required for queries)
    """
    class Meta(AllocationSchema.Meta):
        fields = AllocationSchema.Meta.fields + (
            'resource',
            'used',
            'remaining',
            'percent_used',
            'charges_by_type',
            'adjustments',
            # Shared-allocation tree fields. When `is_inheriting` is True,
            # `used`/`percent_used` reflect the root subtree (the actual
            # shared pool); these expose this project's contribution so
            # consumers can render a two-tone bar.
            'self_used',
            'self_percent_used',
            'root_projcode',
            # Current-snapshot fields (disk resources only — null for
            # other types). Distinct from the cumulative `used` above:
            # answers "how full are you right now?" using the latest
            # disk_charge_summary snapshot, not summed over time.
            'current_used_bytes',
            'current_used_tib',
            'current_snapshot_date',
            'current_pct_used',
        )

    # Add resource details
    resource = fields.Method('get_resource')

    # Usage calculations
    used = fields.Method('get_used')
    remaining = fields.Method('get_remaining')
    percent_used = fields.Method('get_percent_used')
    charges_by_type = fields.Method('get_charges_by_type')
    adjustments = fields.Method('get_adjustments')
    self_used = fields.Method('get_self_used')
    self_percent_used = fields.Method('get_self_percent_used')
    root_projcode = fields.Method('get_root_projcode')
    current_used_bytes = fields.Method('get_current_used_bytes')
    current_used_tib = fields.Method('get_current_used_tib')
    current_snapshot_date = fields.Method('get_current_snapshot_date')
    current_pct_used = fields.Method('get_current_pct_used')

    def get_resource(self, obj):
        """Get resource from account context."""
        account = self.context.get('account')
        if account and account.resource:
            return ResourceSummarySchema().dump(account.resource)
        return None

    def _live_sums(self, obj):
        """``{key: {'charges_by_type', 'adjustment'}}`` for ``obj`` (and ``'root'`` when
        inheriting), one kernel call per allocation per dump."""
        memo = self.__dict__.setdefault('_sums', {})
        if obj.allocation_id in memo:
            return memo[obj.allocation_id]
        account = self.context['account']
        session = self.context['session']
        include_adjustments = self.context.get('include_adjustments', True)
        start_date, end_date = obj.start_date, obj.end_date or datetime.now()
        activity_type = account.resource.activity_type if account.resource else None
        anchors = [usage_anchor('self', account.project, account, activity_type, start_date, end_date)]
        root_account = obj.root.account if obj.is_inheriting else None
        root_project = root_account.project if root_account is not None else None
        if root_project is not None and root_project.has_tree_coordinates():
            root_type = root_account.resource.activity_type if root_account.resource else None
            anchors.append((usage_anchor('root', root_project, root_account, root_type,
                                         start_date, end_date)[0], True))
        sums = anchored_charges(session, anchors, include_adjustments=include_adjustments)
        if root_project is not None and 'root' in sums:
            sums['root']['projcode'] = root_project.projcode
        memo[obj.allocation_id] = sums
        return sums

    def _calculate_usage(self, obj):
        """``(charges_by_type, adjustments, total_used)`` for this allocation's own anchor."""
        account = self.context.get('account')
        session = self.context.get('session')
        include_adjustments = self.context.get('include_adjustments', True)
        if not account or not session:
            return {}, 0.0, 0.0

        row = self.context.get('state')
        if row is not None:
            charges = {k: float(v) for k, v in row.charges_by_type.items()}
            adjustments = row.adjustments if include_adjustments else 0.0
        else:
            own = self._live_sums(obj).get('self', {'charges_by_type': {}, 'adjustment': 0.0})
            charges, adjustments = dict(own['charges_by_type']), own['adjustment']
        activity_type = account.resource.activity_type if account.resource else None
        _keep_fixed_keys(charges, activity_type)
        return charges, adjustments, sum(charges.values()) + adjustments

    def get_charges_by_type(self, obj):
        """Get breakdown of charges by type (comp, dav, disk, archive)."""
        charges, _, _ = self._calculate_usage(obj)
        return charges

    def get_adjustments(self, obj):
        """Get manual charge adjustments total."""
        _, adjustments, _ = self._calculate_usage(obj)
        return adjustments

    def _calculate_tree_usage(self, obj):
        """``(tree_used, root_projcode)`` for an inheriting allocation: the root project's
        full subtree is the shared pool's consumption. ``(None, None)`` otherwise."""
        if not obj.is_inheriting or not self.context.get('session') or not self.context.get('account'):
            return None, None
        include_adjustments = self.context.get('include_adjustments', True)
        row = self.context.get('state')
        if row is not None and include_adjustments and row.root_projcode is not None:
            return row.used, row.root_projcode
        root = self._live_sums(obj).get('root')
        if root is None:
            return None, None
        return sum(root['charges_by_type'].values()) + root['adjustment'], root['projcode']

    def get_used(self, obj):
        """Total used amount. For inheriting allocations, this is the
        root project's full subtree consumption (the actual shared pool
        usage). For standalone allocations, this is the single-account
        charges + adjustments. For DISK it is the occupancy at the latest
        snapshot (TiB against a TiB allocation) of the pool root's subtree,
        as on every other surface; the TiB-year integral stays in `charges_by_type`.
        """
        cap = self._disk_capacity(obj, pool=True)
        if cap is not None:
            return cap['used_tib']
        tree_used, _ = self._calculate_tree_usage(obj)
        if tree_used is not None:
            return tree_used
        _, _, used = self._calculate_usage(obj)
        return used

    def get_remaining(self, obj):
        """Remaining = allocated - used (tree-aware when inheriting)."""
        used = self.get_used(obj)
        allocated = float(obj.amount) if obj.amount else 0.0
        return allocated - used

    def get_percent_used(self, obj):
        """Percent used (tree-aware when inheriting)."""
        used = self.get_used(obj)
        allocated = float(obj.amount) if obj.amount else 0.0
        if allocated > 0:
            return (used / allocated) * 100.0
        return 0.0

    def get_self_used(self, obj):
        """This project's contribution to the shared allocation pool (on DISK, its own
        subtree's occupancy). None for non-inheriting allocations.
        """
        if not obj.is_inheriting:
            return None
        cap = self._disk_capacity(obj)
        if cap is not None:
            return cap['used_tib']
        _, _, used = self._calculate_usage(obj)
        return used

    def get_self_percent_used(self, obj):
        """This project's contribution as a percentage of the shared pool.
        None for non-inheriting allocations.
        """
        used = self.get_self_used(obj)
        if used is None:
            return None
        allocated = float(obj.amount) if obj.amount else 0.0
        if allocated > 0:
            return (used / allocated) * 100.0
        return 0.0

    def get_root_projcode(self, obj):
        """Projcode of the project owning the root allocation. None for
        non-inheriting allocations.
        """
        if not obj.is_inheriting:
            return None
        _, root_projcode = self._calculate_tree_usage(obj)
        return root_projcode

    def _disk_capacity(self, obj, pool=False):
        """Snapshot occupancy for a DISK allocation, else None: this project's subtree, or
        with ``pool`` on a shared allocation, the pool root's.

        The same `bulk_get_subtree_disk_capacity` figure the dashboards use;
        a read-model row supplies it directly. Memoized per schema instance
        because every Method field calls it.
        """
        account = self.context.get('account')
        session = self.context.get('session')
        if not account or not session:
            return None
        if not account.resource or not account.resource.resource_type:
            return None
        if account.resource.resource_type.resource_type != 'DISK':
            return None
        row = self.context.get('state')
        if row is not None:
            tib = row.self_used if row.is_inheriting and not pool else row.used
            return {'used_tib': tib,
                    'used_bytes': int(round(tib * BYTES_PER_TIB)),
                    'activity_date': row.activity_date}
        project = account.project
        if pool and obj.is_inheriting:
            root_account = obj.root.account
            root = root_account.project if root_account is not None else None
            if root is not None and root.has_tree_coordinates():
                project = root
        memo = self.__dict__.setdefault('_disk_caps', {})
        key = (project.project_id, account.resource.resource_name)
        if key not in memo:
            from sam.queries.disk_usage import bulk_get_subtree_disk_capacity
            memo[key] = bulk_get_subtree_disk_capacity(
                session, [(project, account.resource.resource_name)]).get(key)
        return memo[key]

    def _current_snapshot(self, obj):
        """The disk capacity dict behind `used` when a snapshot exists in that subtree."""
        cap = self._disk_capacity(obj, pool=True)
        return cap if cap is not None and cap['activity_date'] is not None else None

    def get_current_used_bytes(self, obj):
        cap = self._current_snapshot(obj)
        return cap['used_bytes'] if cap is not None else None

    def get_current_used_tib(self, obj):
        cap = self._current_snapshot(obj)
        return cap['used_tib'] if cap is not None else None

    def get_current_snapshot_date(self, obj):
        cap = self._current_snapshot(obj)
        return cap['activity_date'] if cap is not None else None

    def get_current_pct_used(self, obj):
        cap = self._current_snapshot(obj)
        if cap is None:
            return None
        allocated = float(obj.amount) if obj.amount else 0.0
        if allocated <= 0:
            return 0.0
        return (cap['used_tib'] / allocated) * 100.0


class AccountSchema(BaseSchema):
    """
    Account schema with allocations.

    Links projects to resources and contains allocations.
    """
    class Meta(BaseSchema.Meta):
        model = Account
        fields = (
            'account_id',
            'project',
            'resource',
            'active_allocation',
            'creation_time',
            'modified_time',
        )

    # Nested relationships
    project = fields.Nested(ProjectSummarySchema)
    resource = fields.Nested(ResourceSummarySchema)
    active_allocation = fields.Method('get_active_allocation')

    def get_active_allocation(self, obj):
        """Get currently active allocation for this account."""
        now = datetime.now()
        for alloc in obj.allocations:
            if alloc.is_active_at(now):
                # Use AllocationWithUsageSchema to include usage calculations
                schema = AllocationWithUsageSchema()
                schema.context = {
                    'account': obj,
                    'session': self.context.get('session'),
                    'include_adjustments': self.context.get('include_adjustments', True)
                }
                return schema.dump(alloc)
        return None
