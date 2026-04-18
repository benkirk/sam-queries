"""
Marshmallow form validation schemas for charge-adjustment routes.

Covers: Create Charge Adjustment (Allocations dashboard → Adjustments tab).
"""

import marshmallow.fields as f
import marshmallow.validate as v

from . import HtmxFormSchema


class CreateChargeAdjustmentForm(HtmxFormSchema):
    """Validate a staff-submitted charge adjustment.

    ``project_id`` comes from the fk_search_field picker's hidden input
    (populated by /static/js/fk-picker.js when a search result is clicked).
    The schema only coerces to int; FK existence (does the Project row
    exist? does the (project, resource) → Account row exist?) is a DB-backed
    check and stays in the route per CLAUDE.md §9.

    ``charge_adjustment_type_id`` membership (the set of types exposed by
    the webapp) is likewise a DB-backed check: the supported set lives in
    ``sam.accounting.adjustments._SIGN_BY_TYPE`` keyed by name, and is
    resolved to IDs at runtime via ``ChargeAdjustment.supported_types()``.
    ``ChargeAdjustment.create()`` raises ``ValueError`` if an unsupported
    type slips through, which the route surfaces as a form error.

    ``amount`` is input-neutral-positive: the user enters a positive
    number; ``ChargeAdjustment.create()`` applies the sign by type.
    """

    project_id = f.Int(required=True)
    resource_id = f.Int(required=True)
    charge_adjustment_type_id = f.Int(required=True)
    amount = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    comment = f.Str(load_default=None)
