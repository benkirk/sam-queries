"""
Marshmallow form validation schemas for Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups, Contract Sources, Contracts, NSF Programs.
"""

import re as _re
import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import validates, ValidationError, post_load

from . import HtmxFormSchema


class EditOrganizationForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    acronym = f.Str(required=True, validate=v.Length(min=1))
    description = f.Str(load_default=None)
    active = f.Bool(load_default=False)


class CreateOrganizationForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    acronym = f.Str(required=True, validate=v.Length(min=1))
    description = f.Str(load_default=None)
    parent_org_id = f.Int(load_default=None)


class EditInstitutionTypeForm(HtmxFormSchema):
    type = f.Str(required=True, validate=v.Length(min=1))


class CreateInstitutionTypeForm(HtmxFormSchema):
    type = f.Str(required=True, validate=v.Length(min=1))


class EditInstitutionForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    acronym = f.Str(required=True, validate=v.Length(min=1))
    nsf_org_code = f.Str(load_default=None)
    address = f.Str(load_default=None)
    city = f.Str(load_default=None)
    zip = f.Str(load_default=None)
    code = f.Str(load_default=None)


class CreateInstitutionForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    acronym = f.Str(required=True, validate=v.Length(min=1))
    institution_type_id = f.Int(required=True)
    nsf_org_code = f.Str(load_default=None)
    city = f.Str(load_default=None)
    code = f.Str(load_default=None)


class CreateMnemonicCodeForm(HtmxFormSchema):
    code = f.Str(required=True)
    description = f.Str(required=True, validate=v.Length(min=1))

    @post_load
    def normalize_code(self, data, **kwargs):
        data['code'] = data['code'].strip().upper()
        return data

    @validates('code')
    def validate_code(self, value, **kwargs):
        code = value.strip().upper()
        if not code:
            raise ValidationError('Code is required.')
        if not _re.match(r'^[A-Z]{3}$', code):
            raise ValidationError('Code must be exactly 3 uppercase letters (A–Z).')


class EditAoiGroupForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    active = f.Bool(load_default=False)


class CreateAoiGroupForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))


class EditAoiForm(HtmxFormSchema):
    area_of_interest = f.Str(required=True, validate=v.Length(min=1))
    area_of_interest_group_id = f.Int(required=True)
    active = f.Bool(load_default=False)


class CreateAoiForm(HtmxFormSchema):
    area_of_interest = f.Str(required=True, validate=v.Length(min=1))
    area_of_interest_group_id = f.Int(required=True)


class EditContractSourceForm(HtmxFormSchema):
    contract_source = f.Str(required=True, validate=v.Length(min=1))
    active = f.Bool(load_default=False)


class CreateContractSourceForm(HtmxFormSchema):
    contract_source = f.Str(required=True, validate=v.Length(min=1))


class EditContractForm(HtmxFormSchema):
    title = f.Str(required=True, validate=v.Length(min=1))
    url = f.Str(load_default=None)
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data


class CreateContractForm(HtmxFormSchema):
    contract_number = f.Str(required=True, validate=v.Length(min=1, max=50))
    title = f.Str(required=True, validate=v.Length(min=1, max=255))
    url = f.Str(load_default=None)
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    contract_source_id = f.Int(required=True)
    principal_investigator_user_id = f.Int(required=True)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data


class EditNsfProgramForm(HtmxFormSchema):
    nsf_program_name = f.Str(required=True, validate=v.Length(min=1))
    active = f.Bool(load_default=False)


class CreateNsfProgramForm(HtmxFormSchema):
    nsf_program_name = f.Str(required=True, validate=v.Length(min=1))
