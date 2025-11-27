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
from sqlalchemy import func
from . import BaseSchema
from .resource import ResourceSummarySchema
from .project import ProjectSummarySchema
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.accounting.adjustments import ChargeAdjustment
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


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
        )

    # Add resource details
    resource = fields.Method('get_resource')

    # Usage calculations
    used = fields.Method('get_used')
    remaining = fields.Method('get_remaining')
    percent_used = fields.Method('get_percent_used')
    charges_by_type = fields.Method('get_charges_by_type')
    adjustments = fields.Method('get_adjustments')

    def get_resource(self, obj):
        """Get resource from account context."""
        account = self.context.get('account')
        if account and account.resource:
            return ResourceSummarySchema().dump(account.resource)
        return None

    def _calculate_usage(self, obj):
        """
        Calculate usage for this allocation.

        Returns tuple: (charges_by_type, adjustments, total_used)
        """
        account = self.context.get('account')
        session = self.context.get('session')
        include_adjustments = self.context.get('include_adjustments', True)

        if not account or not session:
            return {}, 0.0, 0.0

        # Get date range for queries
        now = datetime.now()
        start_date = obj.start_date
        end_date = obj.end_date or now

        # Get resource type
        resource_type = None
        if account.resource and account.resource.resource_type:
            resource_type = account.resource.resource_type.resource_type

        # Calculate charges by type based on resource type
        charges = self._get_charges_by_resource_type(
            session, account.account_id, resource_type, start_date, end_date
        )

        # Calculate adjustments
        adjustments = 0.0
        if include_adjustments:
            adjustments = session.query(
                func.coalesce(func.sum(ChargeAdjustment.amount), 0)
            ).filter(
                ChargeAdjustment.account_id == account.account_id,
                ChargeAdjustment.adjustment_date >= start_date,
                ChargeAdjustment.adjustment_date <= end_date
            ).scalar()
            adjustments = float(adjustments) if adjustments else 0.0

        # Calculate total used
        total_charges = sum(charges.values())
        total_used = total_charges + adjustments

        return charges, adjustments, total_used

    def _get_charges_by_resource_type(self, session, account_id, resource_type, start_date, end_date):
        """
        Query appropriate charge summary tables based on resource type.

        Matches the logic from Project._get_charges_by_resource_type().
        """
        charges = {}

        # HPC & DAV resources - may have both comp and dav charges
        if resource_type in ('HPC', 'DAV'):
            comp = session.query(
                func.coalesce(func.sum(CompChargeSummary.charges), 0)
            ).filter(
                CompChargeSummary.account_id == account_id,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date
            ).scalar()
            if comp:
                charges['comp'] = float(comp)

            dav = session.query(
                func.coalesce(func.sum(DavChargeSummary.charges), 0)
            ).filter(
                DavChargeSummary.account_id == account_id,
                DavChargeSummary.activity_date >= start_date,
                DavChargeSummary.activity_date <= end_date
            ).scalar()
            if dav:
                charges['dav'] = float(dav)

        # DISK resources
        elif resource_type == 'DISK':
            disk = session.query(
                func.coalesce(func.sum(DiskChargeSummary.charges), 0)
            ).filter(
                DiskChargeSummary.account_id == account_id,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date
            ).scalar()
            charges['disk'] = float(disk) if disk else 0.0

        # ARCHIVE resources
        elif resource_type == 'ARCHIVE':
            archive = session.query(
                func.coalesce(func.sum(ArchiveChargeSummary.charges), 0)
            ).filter(
                ArchiveChargeSummary.account_id == account_id,
                ArchiveChargeSummary.activity_date >= start_date,
                ArchiveChargeSummary.activity_date <= end_date
            ).scalar()
            charges['archive'] = float(archive) if archive else 0.0

        return charges

    def get_charges_by_type(self, obj):
        """Get breakdown of charges by type (comp, dav, disk, archive)."""
        charges, _, _ = self._calculate_usage(obj)
        return charges

    def get_adjustments(self, obj):
        """Get manual charge adjustments total."""
        _, adjustments, _ = self._calculate_usage(obj)
        return adjustments

    def get_used(self, obj):
        """Get total used amount (charges + adjustments)."""
        _, _, used = self._calculate_usage(obj)
        return used

    def get_remaining(self, obj):
        """Get remaining allocation (amount - used)."""
        _, _, used = self._calculate_usage(obj)
        allocated = float(obj.amount) if obj.amount else 0.0
        return allocated - used

    def get_percent_used(self, obj):
        """Get percent of allocation used."""
        _, _, used = self._calculate_usage(obj)
        allocated = float(obj.amount) if obj.amount else 0.0
        if allocated > 0:
            return (used / allocated) * 100.0
        return 0.0


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
