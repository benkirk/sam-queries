"""Input schemas for the LDAP sync API's PUTs (JSON from ``sam-ldap-syncd``).

Required-ness and legacy's message wording live in ``sam.manage.ldapsync``; the
field kit is ``sam.schemas.wire``. ``upid`` arrives as an int or a numeric string,
so integers load leniently.
"""

import marshmallow.fields as f

from sam.schemas.wire import CoercedStr, EpochMillis, WireInput, opt_bool, opt_coerced_str, opt_int


def _str(key=None):
    return opt_coerced_str(data_key=key)


def _int(key=None):
    return opt_int(data_key=key)


def _bool(key=None):
    return opt_bool(data_key=key)


class InstitutionSyncInput(WireInput):
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


class OrganizationSyncInput(WireInput):
    organization_id = _int('organizationId')
    name = _str()
    acronym = _str()
    # No load_default: an absent description must not clear SAM's (legacy erased it).
    description = CoercedStr(allow_none=True)
    active = _bool()
    tree_left = _int('treeLeft')
    tree_right = _int('treeRight')
    level = _str()
    level_code = _str('levelCode')
    idms_unique_name = _str('idmsUniqueName')
    deleted = _bool()
    parent_org_acronym = _str('parentOrgAcronym')


class GidAllocationSyncInput(WireInput):
    start_gid = _int('startGid')
    end_gid = _int('endGid')


def _date(key):
    return EpochMillis(data_key=key, allow_none=True, load_default=None)


def _required_bool(key=None):
    """Legacy NPE'd on a null here (a 500); it is a 400."""
    return f.Boolean(data_key=key, required=True, allow_none=False)


class EmailInput(WireInput):
    email = CoercedStr(required=True, allow_none=False)
    primary = _required_bool()


class PhoneInput(WireInput):
    phone_number = CoercedStr(data_key='phoneNumber', required=True, allow_none=False)
    ext_phone_type = _str('extPhoneType')


class CollaborationInput(WireInput):
    employment_id = _int('collaborationId')
    employer_id = _int('institutionId')
    start_date = _date('startDate')
    end_date = _date('endDate')


class PositionInput(WireInput):
    employment_id = _int('positionId')
    employer_id = _int('organizationId')
    start_date = _date('startDate')
    end_date = _date('endDate')
    idms_unique_name = _str('idmsUniqueName')


def _rows(nested):
    """A list; absent is empty (legacy's default), null is a 400 (legacy NPE'd)."""
    return f.List(f.Nested(nested), load_default=list, allow_none=False)


class UserSyncInput(WireInput):
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


class GroupSyncInput(WireInput):
    key = _str()
    posix_gid = _int('posixGid')
    active = _bool()
    tags = f.List(CoercedStr(), load_default=list, allow_none=True)
    usernames = f.List(CoercedStr(), load_default=list, allow_none=True)
