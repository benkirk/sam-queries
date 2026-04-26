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
    """Type-coercion schema for project edit routes.

    Mandatory project identity fields (title, lead, area of interest) are
    required because the edit form always renders them. Other scalar fields
    use ``load_default=None`` so ``project.update()`` can skip unchanged
    values. Checkbox fields use ``load_default=False`` because unchecked
    HTML checkboxes send no key at all — the schema's default then
    correctly represents "unchecked" rather than "unchanged".

    FK existence checks remain in the route (require DB access).
    """
    title = f.Str(required=True, validate=v.Length(min=1, max=255))
    abstract = f.Str(load_default=None)
    area_of_interest_id = f.Int(required=True)
    allocation_type_id = f.Int(load_default=None)
    charging_exempt = f.Bool(load_default=False)
    project_lead_user_id = f.Int(required=True)
    project_admin_user_id = f.Int(load_default=None)
    unix_gid = f.Int(load_default=None)
    ext_alias = f.Str(load_default=None)
    active = f.Bool(load_default=False)


class AddLinkedOrganizationForm(HtmxFormSchema):
    """Validate the organization FK picker when linking an org to a project."""
    organization_id = f.Int(required=True)


class AddLinkedContractForm(HtmxFormSchema):
    """Validate the contract FK picker when linking a contract to a project."""
    contract_id = f.Int(required=True)


def _normalize_directory_suffix(data):
    """Strip + reject any '..' path segment in the directory_suffix field.

    The full directory_name is assembled in the route from the chosen
    DiskResourceRootDirectory + this suffix; the schema doesn't touch the DB.
    """
    suffix = (data.get('directory_suffix') or '').strip()
    if suffix and any(seg == '..' for seg in suffix.split('/')):
        from marshmallow import ValidationError as _VE
        raise _VE({'directory_suffix': ['Suffix cannot contain ".." segments.']})
    data['directory_suffix'] = suffix
    return data


class AddLinkedDirectoryForm(HtmxFormSchema):
    """Add a project_directory: pick a registered disk root + a suffix.

    The route looks up `root_directory_id` (must exist and not equal '/')
    and assembles `directory_name = root.rstrip('/') + ('/' + suffix.lstrip('/') if suffix else '')`.
    """
    root_directory_id = f.Int(required=True)
    directory_suffix = f.Str(load_default='', validate=v.Length(max=255))

    @post_load
    def normalize(self, data, **kwargs):
        return _normalize_directory_suffix(data)


class EditLinkedDirectoryForm(HtmxFormSchema):
    """Edit a project_directory row: rename via root+suffix and/or re-link
    to a project.

    FK existence checks (`root_directory_id` -> DiskResourceRootDirectory,
    `project_id` -> Project) stay in the route since schemas don't touch the DB.
    """
    root_directory_id = f.Int(required=True)
    directory_suffix = f.Str(load_default='', validate=v.Length(max=255))
    project_id = f.Int(required=True)

    @post_load
    def normalize(self, data, **kwargs):
        return _normalize_directory_suffix(data)
