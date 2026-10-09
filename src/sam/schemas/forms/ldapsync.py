"""Input schemas for the LDAP sync API's PUTs (JSON from ``sam-ldap-syncd``).

Plain ``Schema`` with ``unknown=EXCLUDE``: the daemon sends every key SAM's GET
returned plus a few of its own. Types are checked here; required-ness and legacy's
message wording live in ``sam.manage.ldapsync``. ``upid`` arrives as an int or a
numeric string, so integers load leniently.
"""

import marshmallow.fields as f
from marshmallow import EXCLUDE, Schema

from sam.schemas.ldapsync import EpochMillis


class Text(f.String):
    """A string that also accepts a bare number (the daemon's Perl does not keep types)."""

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        return super()._deserialize(value, attr, data, **kwargs)


def _str(key=None):
    return Text(data_key=key, allow_none=True, load_default=None)


def _int(key=None):
    return f.Integer(data_key=key, allow_none=True, load_default=None, strict=False)


def _bool(key=None):
    return f.Boolean(data_key=key, allow_none=True, load_default=None)


class _SyncInput(Schema):
    class Meta:
        unknown = EXCLUDE


class InstitutionSyncInput(_SyncInput):
    institution_id = _int('institutionId')
    name = _str()
    acronym = _str()
    nsf_org_code = _str('nsfOrgCode')
    address = _str()
    city = _str()
    zip = _str()
    country = _str()
    state = _str()
    institution_type = _str('institutionType')
    deleted = _bool()


class OrganizationSyncInput(_SyncInput):
    organization_id = _int('organizationId')
    name = _str()
    acronym = _str()
    # No load_default: an absent description must not clear SAM's (legacy erased it).
    description = Text(allow_none=True)
    active = _bool()
    tree_left = _int('treeLeft')
    tree_right = _int('treeRight')
    level = _str()
    level_code = _str('levelCode')
    idms_unique_name = _str('idmsUniqueName')
    deleted = _bool()
    parent_org_acronym = _str('parentOrgAcronym')


class GidAllocationSyncInput(_SyncInput):
    start_gid = _int('startGid')
    end_gid = _int('endGid')


def _date(key):
    return EpochMillis(data_key=key, allow_none=True, load_default=None)


def _required_bool(key=None):
    """Legacy NPE'd on a null here (a 500); it is a 400."""
    return f.Boolean(data_key=key, required=True, allow_none=False)


class EmailInput(_SyncInput):
    email = Text(required=True, allow_none=False)
    primary = _required_bool()


class PhoneInput(_SyncInput):
    phone_number = Text(data_key='phoneNumber', required=True, allow_none=False)
    ext_phone_type = _str('extPhoneType')


class CollaborationInput(_SyncInput):
    employment_id = _int('collaborationId')
    employer_id = _int('institutionId')
    start_date = _date('startDate')
    end_date = _date('endDate')


class PositionInput(_SyncInput):
    employment_id = _int('positionId')
    employer_id = _int('organizationId')
    start_date = _date('startDate')
    end_date = _date('endDate')
    idms_unique_name = _str('idmsUniqueName')


def _rows(nested):
    """A list; absent is empty (legacy's default), null is a 400 (legacy NPE'd)."""
    return f.List(f.Nested(nested), load_default=list, allow_none=False)


class UserSyncInput(_SyncInput):
    user_name = _str('userName')
    unix_uid = _int('unixUid')
    upid = _int()
    active = _required_bool()
    locked = _required_bool()
    charging_exempt = _required_bool('chargingExempt')
    title = _str()
    first_name = _str('firstname')
    middle_name = _str('middlename')
    last_name = _str('lastname')
    nickname = _str()
    name_suffix = _str('nameSuffix')
    token_type = _str('tokenType')
    type_of_login = _str('typeOfLogin')
    academic_status = _str('academicStatus')
    contact_person_upid = _int('contactPersonUpid')
    deleted = _bool()
    emails = _rows(EmailInput)
    phones = _rows(PhoneInput)
    collaborations = _rows(CollaborationInput)
    positions = _rows(PositionInput)


class GroupSyncInput(_SyncInput):
    key = _str()
    posix_gid = _int('posixGid')
    active = _bool()
    tags = f.List(Text(), load_default=list, allow_none=True)
    usernames = f.List(Text(), load_default=list, allow_none=True)
