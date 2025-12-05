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

from . import BaseSchema
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


class ChargeSummaryBaseSchema(BaseSchema):
    """
    Base schema for all charge summaries.
    """
    class Meta(BaseSchema.Meta):
        fields = ('activity_date', 'account_id', 'user_id', 'charges')


class ComputeChargeSummaryBaseSchema(ChargeSummaryBaseSchema):
    """
    Base schema for computational (HPC/DAV) charge summaries.
    """
    class Meta(ChargeSummaryBaseSchema.Meta):
        fields = ChargeSummaryBaseSchema.Meta.fields + ('num_jobs', 'core_hours')


class StorageChargeSummaryBaseSchema(ChargeSummaryBaseSchema):
    """
    Base schema for storage (Disk/Archive) charge summaries.
    """
    class Meta(ChargeSummaryBaseSchema.Meta):
        fields = ChargeSummaryBaseSchema.Meta.fields + ('number_of_files', 'bytes', 'terabyte_years')


class CompChargeSummarySchema(ComputeChargeSummaryBaseSchema):
    """
    Schema for HPC computational charge summaries.
    """
    class Meta(ComputeChargeSummaryBaseSchema.Meta):
        model = CompChargeSummary
        fields = ComputeChargeSummaryBaseSchema.Meta.fields + ('charge_summary_id',)


class DavChargeSummarySchema(ComputeChargeSummaryBaseSchema):
    """
    Schema for DAV (Data Analysis and Visualization) charge summaries.
    """
    class Meta(ComputeChargeSummaryBaseSchema.Meta):
        model = DavChargeSummary
        fields = ComputeChargeSummaryBaseSchema.Meta.fields + ('dav_charge_summary_id',)


class DiskChargeSummarySchema(StorageChargeSummaryBaseSchema):
    """
    Schema for disk storage charge summaries.
    """
    class Meta(StorageChargeSummaryBaseSchema.Meta):
        model = DiskChargeSummary
        fields = StorageChargeSummaryBaseSchema.Meta.fields + ('disk_charge_summary_id',)


class ArchiveChargeSummarySchema(StorageChargeSummaryBaseSchema):
    """
    Schema for archive/HPSS charge summaries.
    """
    class Meta(StorageChargeSummaryBaseSchema.Meta):
        model = ArchiveChargeSummary
        fields = StorageChargeSummaryBaseSchema.Meta.fields + ('archive_charge_summary_id',)