"""
Marshmallow form validation schemas for HTMX route handlers.

These are standalone marshmallow.Schema subclasses — NOT SQLAlchemyAutoSchema.
Form validation is a separate concern from ORM serialization, so they live
in their own package and import from marshmallow directly.

Usage in a route handler:
    from sam.schemas.forms.facilities import EditFacilityForm
    from marshmallow import ValidationError

    schema = EditFacilityForm()
    try:
        data = schema.load(request.form)
    except ValidationError as e:
        errors = EditFacilityForm.flatten_errors(e.messages)
        return render_template(..., errors=errors, form=request.form)
    # data is a clean dict with coerced Python types
"""

from datetime import datetime
import marshmallow.fields as f
from marshmallow import Schema, EXCLUDE, ValidationError, pre_load


class HtmxFormSchema(Schema):
    """Base schema for all HTMX form validation.

    Configuration:
    - unknown=EXCLUDE: silently ignore extra form fields (CSRF tokens, etc.)
    - @pre_load strips empty-string values so optional Int/Float/Date fields
      fall through to ``load_default`` instead of failing to coerce ``''``.
      This matches the pattern documented in CLAUDE.md §9 (dropping empties
      from ``request.form``) but moves it into the schema layer so every
      HTMX route benefits automatically.
    """

    class Meta:
        unknown = EXCLUDE

    @pre_load
    def _strip_empty_strings(self, data, **kwargs):
        """Normalize Flask's ImmutableMultiDict into a plain dict and drop
        keys whose value is the empty string.

        - For Flask ``ImmutableMultiDict`` input: walk the schema's declared
          fields. ``fields.List`` fields are always read via ``getlist`` so a
          single-checked checkbox produces a one-element list (not a scalar).
          All other fields read a single value via ``.get`` and are dropped if
          the value is ``''``.
        - For plain-dict input (e.g. a route that pre-built the payload):
          drop keys whose value is ``''``. Any already-built list fields pass
          through unchanged.

        This eliminates the need for per-schema ``coerce_empty`` post-loads
        and matches the pattern documented in CLAUDE.md §9 (dropping empties
        from ``request.form``) but moves it into the schema layer so every
        HTMX route benefits automatically.
        """
        from marshmallow.fields import List as _List
        if hasattr(data, 'getlist'):
            out = {}
            for key, field in self.fields.items():
                if isinstance(field, _List):
                    values = [v for v in data.getlist(key) if v != '']
                    if values:
                        out[key] = values
                else:
                    v = data.get(key)
                    if v is not None and v != '':
                        out[key] = v
            return out
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ''}
        return data

    @staticmethod
    def flatten_errors(messages: dict) -> list[str]:
        """Convert {'field': ['msg', ...]} to a flat list for templates.

        Example:
            {'description': ['Missing data for required field.']}
            → ['Description: Missing data for required field.']
        """
        out = []
        for field, msgs in messages.items():
            label = field.replace('_', ' ').title()
            for msg in (msgs if isinstance(msgs, list) else [msgs]):
                out.append(f'{label}: {msg}')
        return out

    @staticmethod
    def normalize_end_date(end_str):
        """Parse a YYYY-MM-DD form input into an end-of-day datetime, or None.

        Accepts an empty string or None and returns None. Otherwise delegates to
        parse_input_end_date which yields a 23:59:59 timestamp so date-range
        comparisons against datetime fields are inclusive of the chosen day.
        """
        if not end_str:
            return None
        from webapp.api.helpers import parse_input_end_date
        return parse_input_end_date(end_str)

    @staticmethod
    def assert_date_range(start_date, end_date, *,
                          field='end_date',
                          message='End date must be after start date.'):
        """Raise ValidationError if end_date <= start_date.

        `start_date` may be a date or datetime; it is normalized to a
        midnight datetime for the comparison. `end_date` should already be
        a datetime (use normalize_end_date first). No-op if either is None.
        """
        if not (start_date and end_date):
            return
        if hasattr(start_date, 'time') and not hasattr(start_date, 'hour'):
            # plain date — combine to datetime at midnight
            start = datetime.combine(start_date, datetime.min.time())
        elif hasattr(start_date, 'hour'):
            start = start_date
        else:
            start = datetime.combine(start_date, datetime.min.time())
        if end_date <= start:
            raise ValidationError({field: [message]})


# Re-export domain schemas for convenience
from .facilities import (
    EditFacilityForm,
    CreateFacilityForm,
    CreatePanelForm,
    EditPanelSessionForm,
    EditAllocationTypeForm,
    CreateAllocationTypeForm,
)
from .resources import (
    EditResourceForm,
    CreateResourceForm,
    EditResourceTypeForm,
    CreateResourceTypeForm,
    EditMachineForm,
    CreateMachineForm,
    EditQueueForm,
)
from .orgs import (
    EditOrganizationForm,
    CreateOrganizationForm,
    EditInstitutionTypeForm,
    CreateInstitutionTypeForm,
    EditInstitutionForm,
    CreateInstitutionForm,
    CreateMnemonicCodeForm,
    EditAoiGroupForm,
    CreateAoiGroupForm,
    EditAoiForm,
    CreateAoiForm,
    EditContractSourceForm,
    CreateContractSourceForm,
    EditContractForm,
    CreateContractForm,
    EditNsfProgramForm,
    CreateNsfProgramForm,
)
from .projects import (
    CreateProjectForm,
    EditProjectForm,
    AddLinkedOrganizationForm,
    AddLinkedContractForm,
    AddLinkedDirectoryForm,
)
from .user import (
    AddMemberForm,
    EditAllocationForm,
    AddAllocationForm,
    RenewAllocationsForm,
    ExtendAllocationsForm,
    SetShellForm,
)

__all__ = [
    'HtmxFormSchema',
    # Facilities
    'EditFacilityForm',
    'CreateFacilityForm',
    'CreatePanelForm',
    'EditPanelSessionForm',
    'EditAllocationTypeForm',
    'CreateAllocationTypeForm',
    # Resources
    'EditResourceForm',
    'CreateResourceForm',
    'EditResourceTypeForm',
    'CreateResourceTypeForm',
    'EditMachineForm',
    'CreateMachineForm',
    'EditQueueForm',
    # Organizations
    'EditOrganizationForm',
    'CreateOrganizationForm',
    'EditInstitutionTypeForm',
    'CreateInstitutionTypeForm',
    'EditInstitutionForm',
    'CreateInstitutionForm',
    'CreateMnemonicCodeForm',
    'EditAoiGroupForm',
    'CreateAoiGroupForm',
    'EditAoiForm',
    'CreateAoiForm',
    'EditContractSourceForm',
    'CreateContractSourceForm',
    'EditContractForm',
    'CreateContractForm',
    'EditNsfProgramForm',
    'CreateNsfProgramForm',
    # Projects
    'CreateProjectForm',
    'EditProjectForm',
    'AddLinkedOrganizationForm',
    'AddLinkedContractForm',
    'AddLinkedDirectoryForm',
    # User
    'AddMemberForm',
    'EditAllocationForm',
    'AddAllocationForm',
    'RenewAllocationsForm',
    'ExtendAllocationsForm',
    'SetShellForm',
]
