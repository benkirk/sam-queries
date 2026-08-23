"""Contract schemas for API serialization.

Only ``ContractSummarySchema`` exists, because nothing yet needs more: there is
no ``/api/v1/contracts/`` endpoint, and the webapp's contract card renders from
ORM objects via ``get_contract_detail``. Add List/Full tiers when an endpoint
needs them, not speculatively.

``ProjectSchema.get_contracts`` emits these structured objects, so
``GET /api/v1/projects/<projcode>`` returns ``contracts`` as a list of objects
rather than hand-padded strings. This is also the CLI's primary contract
serializer -- ``sam-search contracts``, ``sam-search awards`` and
``sam-admin contracts --validate`` all build their envelopes from it via
``cli.contracts.builders``.

It dumps fine outside a Flask application context: ``BaseSchema.Meta.sqla_session``
is consulted by ``load()``, never by ``dump()``.
"""

from marshmallow import fields
from . import BaseSchema
from sam.projects.contracts import Contract


class ContractSummarySchema(BaseSchema):
    """
    Minimal contract schema for nested references.

    Carries the identification fields plus the three relationships an operator
    needs to act on a contract (source, the two people, the program), each
    flattened to a bare name rather than a nested object.
    """
    class Meta(BaseSchema.Meta):
        model = Contract
        fields = (
            'contract_id',
            'contract_number',
            'title',
            'contract_source',
            'start_date',
            'end_date',
            'is_active',
            'url',
            'pi_username',
            'monitor_username',
            'nsf_program',
        )

    # Relationships flattened to names; `is_active` is a hybrid, not a column.
    contract_source = fields.Method('get_contract_source')
    pi_username = fields.Method('get_pi_username')
    monitor_username = fields.Method('get_monitor_username')
    nsf_program = fields.Method('get_nsf_program')
    is_active = fields.Method('get_is_active')

    def get_contract_source(self, obj):
        """Get the funding source name."""
        return obj.contract_source.contract_source if obj.contract_source else None

    def get_pi_username(self, obj):
        """Get the principal investigator's username."""
        return (obj.principal_investigator.username
                if obj.principal_investigator else None)

    def get_monitor_username(self, obj):
        """Get the contract monitor's username (nullable column)."""
        return obj.contract_monitor.username if obj.contract_monitor else None

    def get_nsf_program(self, obj):
        """Get the NSF program name (nullable column)."""
        return obj.nsf_program.nsf_program_name if obj.nsf_program else None

    def get_is_active(self, obj):
        """Get the DateRangeMixin hybrid (open = started, not yet ended)."""
        return obj.is_active
