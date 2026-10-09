"""Input schemas for the LDAP sync API's PUTs (JSON from ``sam-ldap-syncd``).

Plain ``Schema`` with ``unknown=EXCLUDE``: the daemon sends every key SAM's GET
returned plus a few of its own. Types are checked here; required-ness and legacy's
message wording live in ``sam.manage.ldapsync``. ``upid`` arrives as an int or a
numeric string, so integers load leniently.
"""

import marshmallow.fields as f
from marshmallow import EXCLUDE, Schema


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
