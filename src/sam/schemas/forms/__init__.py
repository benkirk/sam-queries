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

from marshmallow import Schema, EXCLUDE


class HtmxFormSchema(Schema):
    """Base schema for all HTMX form validation.

    Configuration:
    - unknown=EXCLUDE: silently ignore extra form fields (CSRF tokens, etc.)
    - Empty strings from HTML forms are treated as missing by field defaults.
    """

    class Meta:
        unknown = EXCLUDE

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
]
