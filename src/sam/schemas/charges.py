"""
Charge summary schemas for API serialization.

Provides schemas for daily aggregated charge data from summary tables:
- CompChargeSummarySchema: HPC computational charges
- DavChargeSummarySchema: DAV charges
- DiskChargeSummarySchema: Storage charges
- ArchiveChargeSummarySchema: Archive/HPSS charges

Usage:
    from sam.schemas import CompChargeSummarySchema, DiskChargeSummarySchema

    # Serialize charge summaries
    comp_data = CompChargeSummarySchema(many=True).dump(comp_charges)
    disk_data = DiskChargeSummarySchema(many=True).dump(disk_charges)
"""

from marshmallow import fields
from . import BaseSchema
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


class CompChargeSummarySchema(BaseSchema):
    """
    Schema for HPC computational charge summaries.

    Daily aggregated charges for HPC resources (Derecho, etc.).
    """
    class Meta(BaseSchema.Meta):
        model = CompChargeSummary
        fields = (
            'charge_summary_id',
            'activity_date',
            'account_id',
            'user_id',
            'num_jobs',
            'core_hours',
            'charges',
        )


class DavChargeSummarySchema(BaseSchema):
    """
    Schema for DAV (Data Analysis and Visualization) charge summaries.

    Daily aggregated charges for DAV resources (Casper, etc.).
    """
    class Meta(BaseSchema.Meta):
        model = DavChargeSummary
        fields = (
            'dav_charge_summary_id',
            'activity_date',
            'account_id',
            'user_id',
            'num_jobs',
            'core_hours',
            'charges',
        )


class DiskChargeSummarySchema(BaseSchema):
    """
    Schema for disk storage charge summaries.

    Daily aggregated charges for DISK resources (Stratus, Campaign Store, GLADE).
    """
    class Meta(BaseSchema.Meta):
        model = DiskChargeSummary
        fields = (
            'disk_charge_summary_id',
            'activity_date',
            'account_id',
            'user_id',
            'number_of_files',
            'bytes',
            'terabyte_years',
            'charges',
        )


class ArchiveChargeSummarySchema(BaseSchema):
    """
    Schema for archive/HPSS charge summaries.

    Daily aggregated charges for ARCHIVE resources (HPSS).
    """
    class Meta(BaseSchema.Meta):
        model = ArchiveChargeSummary
        fields = (
            'archive_charge_summary_id',
            'activity_date',
            'account_id',
            'user_id',
            'number_of_files',
            'bytes',
            'terabyte_years',
            'charges',
        )
