"""Output schemas for the HEUV API (``/api/protected/heuv/v1``), legacy Java SAM's portal feed.

Fields are declared in legacy's Jackson order (field-backed properties first, then
getter-only ones such as ``userCount``); nulls are emitted. Inputs are the plain dicts
of ``sam.queries.heuv``, which does all rounding. Contract: ``docs/apis/HEUV_API.md``.
"""

from marshmallow import Schema, fields

from sam.schemas.wire import UtcDateTime, Ymd

_Str = lambda **kw: fields.String(allow_none=True, **kw)        # noqa: E731
_Int = lambda **kw: fields.Integer(allow_none=True, **kw)       # noqa: E731
_Bool = lambda **kw: fields.Boolean(allow_none=True, **kw)      # noqa: E731


class DefaultProjectSchema(Schema):
    username = _Str()
    resource_name = _Str(data_key='resourceName')
    projcode = _Str()


class UserGroupSchema(Schema):
    username = _Str()
    group_name = _Str(data_key='groupName')
    unix_gid = _Int(data_key='unixGid')
    primary = _Bool()
    project = _Bool()
    projcode = _Str()


class UserAccessSchema(Schema):
    username = _Str()
    resource_name = _Str(data_key='resourceName')
    resource_type = _Str(data_key='resourceType')
    home_directory = _Str(data_key='homeDirectory')
    shell_name = _Str(data_key='shellName')


class ResourceAssignmentSchema(Schema):
    resource_name = _Str(data_key='resourceName')
    start_date = UtcDateTime(data_key='startDate')
    end_date = UtcDateTime(data_key='endDate')


class AssignedProjectSchema(Schema):
    projcode = _Str()
    primary = _Bool()
    title = _Str()
    resource_assignments = fields.List(fields.Nested(ResourceAssignmentSchema), data_key='resourceAssignments')


class ProjectAssignmentSchema(Schema):
    projcode = _Str()
    primary = _Bool()
    title = _Str()
    start_date = UtcDateTime(data_key='startDate')
    end_date = UtcDateTime(data_key='endDate')


class AssignedResourceSchema(Schema):
    resource_name = _Str(data_key='resourceName')
    project_assignments = fields.List(fields.Nested(ProjectAssignmentSchema), data_key='projectAssignments')


class ExemptionSchema(Schema):
    active = _Bool()
    start_date = Ymd(data_key='startDate')
    end_date = Ymd(data_key='endDate')
    hour_limit = _Int(data_key='hourLimit')
    comment = _Str()


class ExemptionQueueSchema(Schema):
    queue_name = _Str(data_key='queueName')
    exemptions = fields.List(fields.Nested(ExemptionSchema))


class ExemptionResourceSchema(Schema):
    resource_name = _Str(data_key='resourceName')
    queues = fields.List(fields.Nested(ExemptionQueueSchema))


class WallclockExemptionsSchema(Schema):
    username = _Str()
    resources = fields.List(fields.Nested(ExemptionResourceSchema))


class ProjcodeSearchSchema(Schema):
    projcode = _Str()
    title = _Str()


class ReportAllocationSchema(Schema):
    start_date = Ymd(data_key='startDate')
    end_date = Ymd(data_key='endDate')
    active = _Bool()


class ReportAccountSchema(Schema):
    resource_name = _Str(data_key='resourceName')
    threshold_limited = _Bool(data_key='thresholdLimited')
    allocations = fields.List(fields.Nested(ReportAllocationSchema))


class ReportProjectSchema(Schema):
    projcode = _Str()
    title = _Str()
    lead_username = _Str(data_key='leadUsername')
    lead_name = _Str(data_key='leadName')
    admin_username = _Str(data_key='adminUsername')
    admin_name = _Str(data_key='adminName')
    abstract_text = _Str(data_key='abstractText')
    hierarchical = _Bool()
    accounts = fields.List(fields.Nested(ReportAccountSchema))


class ThresholdReportSchema(Schema):
    period = _Int()
    percent_limit = _Int(data_key='percentLimit')
    allocation_amount = _Int(data_key='allocationAmount')
    charges = _Int()
    percent_usage = _Int(data_key='percentUsage')
    label = _Str()


class _AccountReportHead(Schema):
    resource_name = _Str(data_key='resourceName')
    resource_type = _Str(data_key='resourceType')
    resource_usage_type = _Str(data_key='resourceUsageType')
    status = _Str()
    allocation_start_date = Ymd(data_key='allocationStartDate')
    allocation_end_date = Ymd(data_key='allocationEndDate')
    allocation_propagated = _Bool(data_key='allocationPropagated')
    allocation_amount = _Int(data_key='allocationAmount')
    usernames = fields.List(fields.String())


class AccruedChargesReportSchema(_AccountReportHead):
    total_charges = _Int(data_key='totalCharges')
    adjustments = _Int()
    balance = _Int()
    threshold_reports = fields.List(fields.Nested(ThresholdReportSchema), data_key='thresholdReports')
    threshold_limited = _Bool(data_key='thresholdLimited')
    user_count = _Int(data_key='userCount')


class DataHoldingsReportSchema(_AccountReportHead):
    total_holdings = _Int(data_key='totalHoldings')
    number_of_files = _Int(data_key='numberOfFiles')
    balance = _Int()
    user_count = _Int(data_key='userCount')


DATA_HOLDINGS = 'DataHoldings'
ACCRUED_CHARGES = 'AccruedCharges'


class AccountReportField(fields.Field):
    """One ``accountReports`` row, in the variant its ``resource_usage_type`` names."""

    _schemas = {DATA_HOLDINGS: DataHoldingsReportSchema(), ACCRUED_CHARGES: AccruedChargesReportSchema()}

    def _serialize(self, value, attr, obj, **kwargs):
        return self._schemas[value['resource_usage_type']].dump(value)


class ProjectUsageReportSchema(Schema):
    projcode = _Str()
    report_date = Ymd(data_key='reportDate')
    account_reports = fields.List(AccountReportField(), data_key='accountReports')


class ProjectHierarchySchema(Schema):
    projcode = _Str()
    parent_projcode = _Str(data_key='parentProjcode')
    root_projcode = _Str(data_key='rootProjcode')
    children = fields.List(fields.Nested(lambda: ProjectHierarchySchema()))


class ShellSchema(Schema):
    shell_name = _Str(data_key='shellName')
    dflt = _Bool()


class AccessibleResourceSchema(Schema):
    resource_name = _Str(data_key='resourceName')
    resource_type = _Str(data_key='resourceType')
    login = _Bool()
    shells = fields.List(fields.Nested(ShellSchema))
