"""
Contract schemas for API serialization.

Provides one level today:
- ContractSummarySchema: Minimal fields for nested references

Only the Summary tier exists because nothing yet needs more: there is no
``/api/v1/contracts/`` endpoint, and the webapp's contract card renders from
ORM objects via ``get_contract_detail``. Add List/Full tiers when an endpoint
actually needs them rather than speculatively.

**This schema changed a public API response shape.**
``ProjectSchema.get_contracts`` used to emit hand-padded f-strings and now
returns these structured objects, so ``GET /api/v1/projects/<projcode>``
went from a list of ``f"{source} {number:<20} {title}"`` strings::

    "contracts": ["NSF AGS-1852977          The Management and Operation of …"]

to a list of objects carrying the fields below (``contract_id``,
``contract_number``, ``title``, ``contract_source``, ``start_date``,
``end_date``, ``is_active``, ``url``, ``pi_username``, ``monitor_username``,
``nsf_program``)::

    "contracts": [{"contract_number": "AGS-1852977", "contract_source": "NSF",
                   "title": "…", "is_active": true, …}]

Shipped together in #403. (An earlier draft of this docstring called the
repoint a deferred follow-up; it was not deferred, and saying so misled
readers into believing the response shape was unchanged.)

Beyond the API, this is also the CLI's primary serializer — ``sam-search
contracts``, ``sam-search awards`` and ``sam-admin contracts --validate`` all
build their JSON envelopes from it via ``cli.contracts.builders``.

Usage:
    from sam.schemas import ContractSummarySchema

    # Nested reference (minimal)
    summary_data = ContractSummarySchema().dump(contract)
    summaries = ContractSummarySchema(many=True).dump(contracts)

Note this dumps fine outside a Flask application context — ``BaseSchema.Meta.
sqla_session`` is only consulted by ``load()``, never by ``dump()`` — which is
what lets ``sam-admin contracts --validate`` use it.
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
