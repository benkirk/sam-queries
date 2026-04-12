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
    def validate_projcode(self, value):
        code = value.strip().upper()
        if not code:
            raise ValidationError('Project code is required.')
        if not re.fullmatch(r'[A-Z0-9]{2,30}', code):
            raise ValidationError('Project code must be 2–30 uppercase letters/digits.')

    @post_load
    def normalize(self, data, **kwargs):
        data['projcode'] = data['projcode'].strip().upper()
        if data.get('abstract') == '':
            data['abstract'] = None
        if data.get('ext_alias') == '':
            data['ext_alias'] = None
        # charging_exempt from checkbox comes as 'on' not a bool
        return data
