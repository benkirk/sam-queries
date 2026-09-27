"""Form schemas for Admin > Roles & access: a grant and a role."""

import marshmallow.fields as f
import marshmallow.validate as v

from sam.security.permissions import Permission
from sam.security.rbac_catalog import SUBJECT_TYPES

from . import HtmxFormSchema

PERMISSION_VALUES = tuple(sorted(p.value for p in Permission))


class AddGrantForm(HtmxFormSchema):
    """One grant. The subject arrives as a user id (picker), a group name or a
    key name; exactly one of role_name / permission is checked by the handler."""

    subject_type = f.Str(required=True, validate=v.OneOf(SUBJECT_TYPES))
    subject_user_id = f.Int(load_default=None)
    subject_name = f.Str(load_default=None, validate=v.Length(max=35))
    role_name = f.Str(load_default=None, validate=v.Length(max=40))
    permission = f.Str(load_default=None, validate=v.OneOf(PERMISSION_VALUES))
    facility_name = f.Str(load_default=None, validate=v.Length(max=40))
    note = f.Str(load_default=None, validate=v.Length(max=255))


class SaveRoleForm(HtmxFormSchema):
    """A role's name, parent and direct permissions (multi-checkbox)."""

    name = f.Str(required=True, validate=[
        v.Length(min=1, max=40),
        v.Regexp(r'^[A-Za-z0-9_-]+$', error='Letters, digits, _ or - only.')])
    description = f.Str(load_default=None, validate=v.Length(max=255))
    extends = f.Str(load_default=None, validate=v.Length(max=40))
    permissions = f.List(f.Str(validate=v.OneOf(PERMISSION_VALUES)), load_default=[])
