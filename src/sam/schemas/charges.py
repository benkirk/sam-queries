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

from marshmallow import Schema, fields, validate

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


# ---------------------------------------------------------------------------
# Input schemas for POST endpoints (plain marshmallow, not SQLAlchemy-auto)
# ---------------------------------------------------------------------------

class BaseChargeSummaryInputSchema(Schema):
    """Base input schema for all charge summary POST endpoints."""
    activity_date            = fields.Date(required=True)
    act_username             = fields.Str(required=True, validate=validate.Length(max=35))
    act_projcode             = fields.Str(required=True, validate=validate.Length(max=30))
    act_unix_uid             = fields.Int(load_default=None)  # nullable: jobs may lack uid
    resource_name            = fields.Str(required=True, validate=validate.Length(max=40))
    charges                  = fields.Float(required=True)
    # Optional resolved overrides
    username                 = fields.Str(load_default=None, validate=validate.Length(max=35))
    projcode                 = fields.Str(load_default=None, validate=validate.Length(max=30))
    unix_uid                 = fields.Int(load_default=None)
    # Facility override — bypasses [0]-index heuristic on multi-facility resources
    facility_name            = fields.Str(load_default=None, validate=validate.Length(max=30))
    # Allow resolving historically deleted accounts (for backfill)
    include_deleted_accounts = fields.Bool(load_default=False)


class CompChargeSummaryInputSchema(BaseChargeSummaryInputSchema):
    """Input schema for comp charge summary POST endpoint."""
    machine_name            = fields.Str(load_default=None, validate=validate.Length(max=100))
    queue_name              = fields.Str(required=True, validate=validate.Length(max=100))
    resource                = fields.Str(load_default=None)  # resource column override
    num_jobs                = fields.Int(required=True, validate=validate.Range(min=0))
    core_hours              = fields.Float(required=True, validate=validate.Range(min=0))
    cos                     = fields.Int(load_default=None)
    sweep                   = fields.Int(load_default=None)
    error_comment           = fields.Str(load_default=None)
    create_queue_if_missing = fields.Bool(load_default=False)


class StorageChargeSummaryInputSchema(BaseChargeSummaryInputSchema):
    """Base input schema for disk and archive charge summary POST endpoints."""
    number_of_files = fields.Int(load_default=None, validate=validate.Range(min=0))
    bytes           = fields.Int(load_default=None, validate=validate.Range(min=0))
    terabyte_years  = fields.Float(load_default=None, validate=validate.Range(min=0))


class DiskChargeSummaryInputSchema(StorageChargeSummaryInputSchema):
    """Input schema for disk charge summary POST endpoint."""
    pass


class ArchiveChargeSummaryInputSchema(StorageChargeSummaryInputSchema):
    """Input schema for archive charge summary POST endpoint."""
    pass