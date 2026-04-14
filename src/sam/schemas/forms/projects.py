"""
Marshmallow form validation schemas for Project management routes.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import validates, ValidationError, post_load
import re

from . import HtmxFormSchema


class CreateProjectForm(HtmxFormSchema):
    """Basic type-coercion schema for project creation.

    Note: FK existence checks (facility, panel, lead user, etc.) remain in the
    route since they require database access. This schema handles string
    normalization and integer coercions to eliminate the manual try/except
    int-conversion blocks.
    """
    projcode = f.Str(required=True)
    title = f.Str(required=True, validate=v.Length(min=1, max=255))
    abstract = f.Str(load_default=None)
    facility_id = f.Int(required=True)
    panel_id = f.Int(required=True)
    project_lead_user_id = f.Int(required=True)
    project_admin_user_id = f.Int(load_default=None)
    area_of_interest_id = f.Int(required=True)
    allocation_type_id = f.Int(load_default=None)
    parent_id = f.Int(load_default=None)
    contract_id = f.Int(load_default=None)
    organization_id = f.Int(load_default=None)
    charging_exempt = f.Bool(load_default=False)
    unix_gid = f.Int(load_default=None)
    ext_alias = f.Str(load_default=None)

    @validates('projcode')
    def validate_projcode(self, value, **kwargs):
        code = value.strip().upper()
        if not code:
            raise ValidationError('Project code is required.')
        if not re.fullmatch(r'[A-Z0-9]{2,30}', code):
            raise ValidationError('Project code must be 2–30 uppercase letters/digits.')

    @post_load
    def normalize(self, data, **kwargs):
        data['projcode'] = data['projcode'].strip().upper()
        return data


class EditProjectForm(HtmxFormSchema):
    """Partial type-coercion schema for project edit routes.

    All fields are optional so the route can call
    ``EditProjectForm().load(data, partial=True)`` and only update
    whatever the user submitted.

    IMPORTANT — checkbox handling: unchecked HTML checkboxes send *no*
    field at all, so the route must inject explicit ``False`` for boolean
    fields that are absent from ``request.form`` before passing to this
    schema (otherwise a missing field would be silently ignored).

    FK existence checks remain in the route (require DB access).
    """
    title = f.Str(load_default=None, validate=v.Length(min=1, max=255))
    abstract = f.Str(load_default=None)
    area_of_interest_id = f.Int(load_default=None)
    allocation_type_id = f.Int(load_default=None)
    charging_exempt = f.Bool(load_default=None)
    project_lead_user_id = f.Int(load_default=None)
    project_admin_user_id = f.Int(load_default=None)
    unix_gid = f.Int(load_default=None)
    ext_alias = f.Str(load_default=None)
    active = f.Bool(load_default=None)


class AddLinkedOrganizationForm(HtmxFormSchema):
    """Validate the organization FK picker when linking an org to a project."""
    organization_id = f.Int(required=True)


class AddLinkedContractForm(HtmxFormSchema):
    """Validate the contract FK picker when linking a contract to a project."""
    contract_id = f.Int(required=True)


class AddLinkedDirectoryForm(HtmxFormSchema):
    """Validate the directory name field when adding a project directory."""
    directory_name = f.Str(required=True, validate=v.Length(min=1, max=255))

    @post_load
    def normalize(self, data, **kwargs):
        data['directory_name'] = data['directory_name'].strip()
        if not data['directory_name']:
            from marshmallow import ValidationError as _VE
            raise _VE({'directory_name': ['Directory name cannot be blank.']})
        return data
