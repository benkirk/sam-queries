"""Allocations > XRAS > Configuration: the request types as a PI sees them, editable.

Viewed with ``VIEW_XRAS`` (the XRAS tab's permission, not facility-scopable); the guide rows
(``sam/accounting/allocation_type_guide.py``, the card's words) and the request limits
(``sam/accounting/allocation_type_limits.py``, the amounts) are written with ``MANAGE_XRAS``.
A row is retired through its edit form's Active box. Design:
``docs/plans/XRAS_SUBMISSION_SPRINT3.md``.
"""

from flask import render_template, request
from flask_login import current_user, login_required

from sam.accounting.allocation_type_guide import AUDIENCE_LABELS, AllocationTypeGuide
from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource
from sam.schemas.forms import (CreateAllocationLimitForm, CreateAllocationTypeGuideForm,
                               EditAllocationLimitForm, EditAllocationTypeGuideForm)
from sam.xras.audiences import AUDIENCES
from webapp.dashboards.admin.crud import CrudSpec, register_crud
from webapp.extensions import db
from webapp.utils.htmx import modal_triggers, read_tab
from webapp.utils.rbac import Permission, require_permission
from webapp.utils.xras import interactive_config, report_opportunities, type_cards

from .. import bp

_PARTIALS = 'dashboards/allocations/partials'
_GUIDE_FIELDS = ('title', 'allocation_type', 'summary', 'funding', 'cadence', 'notes',
                 'audience', 'display_order')


@bp.route('/xras/configuration')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_configuration():
    """The page: the "Preview as" switch over the lazy type cards."""
    return render_template('dashboards/allocations/xras_configuration.html',
                           audiences=AUDIENCES)


@bp.route('/xras/configuration/cards')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_configuration_cards_fragment():
    """One card per type as *audience* sees it (empty: every type), then the unpublished ones."""
    audience = read_tab('audience', tuple(AUDIENCE_LABELS), '')
    config = interactive_config()
    opportunities, outage = report_opportunities(config)
    cards = type_cards(opportunities, config, audiences={audience} if audience else None,
                       include_unguided=True)
    return render_template(f'{_PARTIALS}/xras_type_cards_htmx.html', cards=cards,
                           outage=outage, audience=audience, audience_labels=AUDIENCE_LABELS)


def _actor() -> str:
    return getattr(current_user, 'username', None) or 'unknown'


def _limit_context():
    """The create form's selects (the active SAM types with their panel, the resources) and
    the type a card's "Add a limit" names (``?allocation_type=``), preset by its first id."""
    types = (db.session.query(AllocationType).filter(AllocationType.is_active)
             .order_by(AllocationType.allocation_type, AllocationType.allocation_type_id).all())
    resources = (db.session.query(Resource).filter(Resource.is_active)
                 .order_by(Resource.resource_name).all())
    named = request.args.get('allocation_type')
    first = next((t.allocation_type_id for t in types if named and t.allocation_type == named),
                 None)
    return {'allocation_types': types, 'resources': resources,
            'preset': {'allocation_type_id': first} if first else {}}


def _guide_context():
    """The select options: the active SAM type names, and the registry's audiences."""
    names = (db.session.query(AllocationType.allocation_type).filter(AllocationType.is_active)
             .distinct().order_by(AllocationType.allocation_type).all())
    return {'allocation_type_options': [(n, n) for (n,) in names],
            'audience_options': list(AUDIENCE_LABELS.items()),
            'preset': {k: request.args[k] for k in ('title', 'allocation_type')
                       if request.args.get(k)}}


register_crud(bp, CrudSpec(
    slug='allocation-limit', name='Request limit',
    model=AllocationTypeResourceLimit, id_param='allocation_type_resource_limit_id',
    context_key='limit', actions=('edit', 'create'),
    edit_schema=EditAllocationLimitForm, create_schema=CreateAllocationLimitForm,
    edit_fields=('min_amount', 'default_amount', 'max_amount', 'active'),
    create_kwargs=lambda data: {
        **{k: data[k] for k in ('allocation_type_id', 'resource_id', 'min_amount',
                                'default_amount', 'max_amount')},
        'created_by': _actor()},
    create_context=_limit_context,
    edit_template=f'{_PARTIALS}/edit_allocation_limit_form_htmx.html',
    create_template=f'{_PARTIALS}/create_allocation_limit_form_htmx.html',
    triggers=modal_triggers('reloadAllocationLimitsCard'),
    edit_permission=Permission.MANAGE_XRAS,
    create_permission=Permission.MANAGE_XRAS,
))


register_crud(bp, CrudSpec(
    slug='allocation-guide', name='Allocation type guide',
    model=AllocationTypeGuide, id_param='allocation_type_guide_id',
    context_key='guide', actions=('edit', 'create'),
    edit_schema=EditAllocationTypeGuideForm, create_schema=CreateAllocationTypeGuideForm,
    edit_fields=_GUIDE_FIELDS + ('active',),
    create_kwargs=lambda data: {
        **{k: data[k] for k in ('xras_allocation_type_id',) + _GUIDE_FIELDS},
        'created_by': _actor()},
    edit_context=_guide_context, create_context=_guide_context,
    edit_template=f'{_PARTIALS}/edit_allocation_guide_form_htmx.html',
    create_template=f'{_PARTIALS}/create_allocation_guide_form_htmx.html',
    triggers=modal_triggers('reloadAllocationGuidesCard'),
    edit_permission=Permission.MANAGE_XRAS,
    create_permission=Permission.MANAGE_XRAS,
))
