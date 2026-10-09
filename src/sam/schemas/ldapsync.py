"""Output schemas for the LDAP sync API (``/api/protected/admin/ldapsync/*``).

Declared in legacy's field order (Jackson emits declaration order; marshmallow 4
keeps it). Inputs are the plain dicts of ``sam.queries.ldapsync``. Nulls are
emitted, as legacy's DTOs carry no ``@JsonInclude``. See ``docs/plans/LDAP_SYNC_API.md``.
"""

from marshmallow import Schema, fields

from sam.dates import from_epoch_millis, to_epoch_millis


class EpochMillis(fields.Field):
    """A naive-Mountain datetime as epoch milliseconds (Jackson's ``java.util.Date``)."""

    def _serialize(self, value, attr, obj, **kwargs):
        return to_epoch_millis(value)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool):
            raise self.make_error('invalid')
        try:
            return from_epoch_millis(value)
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise self.make_error('invalid') from exc

    default_error_messages = {'invalid': 'Not a valid epoch-millisecond timestamp.'}


_Str = lambda **kw: fields.String(allow_none=True, **kw)        # noqa: E731
_Int = lambda **kw: fields.Integer(allow_none=True, **kw)       # noqa: E731
_Bool = lambda **kw: fields.Boolean(allow_none=True, **kw)      # noqa: E731


class InstitutionSyncSchema(Schema):
    institution_id = _Int(data_key='institutionId')
    name = _Str()
    acronym = _Str()
    nsf_org_code = _Str(data_key='nsfOrgCode')
    address = _Str()
    city = _Str()
    zip = _Str()
    country = _Str()
    state = _Str()
    institution_type = _Str(data_key='institutionType')
    deleted = _Bool()


class OrganizationSyncSchema(Schema):
    acronym = _Str()
    active = _Bool()
    description = _Str()
    level_code = _Str(data_key='levelCode')
    level = _Str()
    name = _Str()
    organization_id = _Int(data_key='organizationId')
    parent_org_acronym = _Str(data_key='parentOrgAcronym')
    tree_left = _Int(data_key='treeLeft')
    tree_right = _Int(data_key='treeRight')
    idms_unique_name = _Str(data_key='idmsUniqueName')
    deleted = _Bool()


class CollaborationSyncSchema(Schema):
    collaboration_id = _Int(data_key='collaborationId')
    institution_id = _Int(data_key='institutionId')
    upid = _Int()
    start_date = EpochMillis(data_key='startDate', allow_none=True)
    end_date = EpochMillis(data_key='endDate', allow_none=True)


class PositionSyncSchema(Schema):
    position_id = _Int(data_key='positionId')
    organization_id = _Int(data_key='organizationId')
    upid = _Int()
    start_date = EpochMillis(data_key='startDate', allow_none=True)
    end_date = EpochMillis(data_key='endDate', allow_none=True)
    idms_unique_name = _Str(data_key='idmsUniqueName')


class EmailSyncSchema(Schema):
    email_address_id = _Int(data_key='emailAddressId')
    user_id = _Int(data_key='userId')
    email = _Str()
    primary = _Bool()


class PhoneSyncSchema(Schema):
    ext_phone_id = _Int(data_key='extPhoneId')
    ext_phone_user_id = _Int(data_key='extPhoneUserId')
    ext_phone_type = _Str(data_key='extPhoneType')
    phone_number = _Str(data_key='phoneNumber')


class UserSyncSchema(Schema):
    academic_status = _Str(data_key='academicStatus')
    active = fields.Boolean()
    charging_exempt = _Bool(data_key='chargingExempt')
    collaborations = fields.List(fields.Nested(CollaborationSyncSchema))
    contact_person_upid = _Int(data_key='contactPersonUpid')
    emails = fields.List(fields.Nested(EmailSyncSchema))
    firstname = _Str()
    institution_ids = fields.List(fields.Integer(), data_key='institutionIds')
    lastname = _Str()
    locked = _Bool()
    middlename = _Str()
    name_suffix = _Str(data_key='nameSuffix')
    nickname = _Str()
    org_ids = fields.List(fields.Integer(), data_key='orgIds')
    phones = fields.List(fields.Nested(PhoneSyncSchema))
    positions = fields.List(fields.Nested(PositionSyncSchema))
    title = _Str()
    token_type = _Str(data_key='tokenType')
    type_of_login = _Str(data_key='typeOfLogin')
    unix_uid = _Int(data_key='unixUid')
    upid = _Int()
    user_id = _Int(data_key='userId')
    user_name = _Str(data_key='userName')
    deleted = _Bool()
    preferred_name = _Str(data_key='preferredName')


class GroupSyncSchema(Schema):
    name = _Str()
    key = _Str()
    description = _Str()
    org = _Str()
    active = fields.Boolean()
    posix_gid = _Int(data_key='posixGid')
    usernames = fields.List(fields.String())
    upids = fields.List(fields.Integer())
    rolenames = fields.List(fields.String())
    tags = fields.List(fields.String())


class ProjectGroupSyncSchema(Schema):
    name = _Str()
    key = _Str()
    active = fields.Boolean()
    posix_gid = _Int(data_key='posixGid')
    upids = fields.List(fields.Integer())
    rolenames = fields.List(fields.String())
    tags = fields.List(fields.String())
    last_modified = EpochMillis(data_key='lastModified', allow_none=True)


class GroupTagSyncSchema(Schema):
    name = fields.String()
    access_branch = fields.Boolean(data_key='accessBranch')


class GidAllocationSyncSchema(Schema):
    gid_allocation_id = _Int(data_key='gidAllocationId')
    start_gid = _Int(data_key='startGid')
    next_gid = _Int(data_key='nextGid')
    end_gid = _Int(data_key='endGid')
    creation_time = EpochMillis(data_key='creationTime', allow_none=True)
    modified_time = EpochMillis(data_key='modifiedTime', allow_none=True)


class SyncStatusSchema(Schema):
    institution_update_time = EpochMillis(data_key='institutionUpdateTime', allow_none=True)
    organization_update_time = EpochMillis(data_key='organizationUpdateTime', allow_none=True)
    user_update_time = EpochMillis(data_key='userUpdateTime', allow_none=True)
    group_update_time = EpochMillis(data_key='groupUpdateTime', allow_none=True)
    gid_allocation_update_time = EpochMillis(data_key='gidAllocationUpdateTime', allow_none=True)
    access_branches = fields.List(fields.String(), data_key='accessBranches')


class UserPurgePermitSchema(Schema):
    username = _Str()
    purgeable = fields.Boolean()
    message = _Str()


class GroupPurgePermitSchema(Schema):
    unix_gid = _Int(data_key='unixGid')
    purgeable = fields.Boolean()
    message = _Str()


class InstitutionPurgePermitSchema(Schema):
    institution_id = _Int(data_key='institutionId')
    purgeable = fields.Boolean()
    message = _Str()


class OrganizationPurgePermitSchema(Schema):
    organization_id = _Int(data_key='organizationId')
    purgeable = fields.Boolean()
    message = _Str()
