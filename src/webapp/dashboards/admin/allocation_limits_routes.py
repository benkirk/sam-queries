"""Admin -> Configuration: the request limits per allocation type per resource.

Read with ``VIEW_SYSTEM_CONFIG``, edited with ``SYSTEM_ADMIN``. The rows bound the
amounts a user may request through the My Requests tab; a pair with no active row has
no limit. Model: ``sam/accounting/allocation_type_limits.py``.
"""

from flask import render_template, request
from flask_login import login_required
from sqlalchemy.orm import joinedload

from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource
from sam.schemas.forms import CreateAllocationLimitForm, EditAllocationLimitForm
from webapp.extensions import db
from webapp.utils.htmx import modal_triggers, read_active_only
from webapp.utils.rbac import Permission, require_permission

from .blueprint import bp
from .crud import CrudSpec, register_crud


def _limits(active_only: bool):
    """Every limit, sorted by type name, panel, resource; the joins load in one query."""
    q = (db.session.query(AllocationTypeResourceLimit)
         .options(joinedload(AllocationTypeResourceLimit.allocation_type)
                  .joinedload(AllocationType.panel),
                  joinedload(AllocationTypeResourceLimit.resource)
                  .joinedload(Resource.resource_type)))
    if active_only:
        q = q.filter(AllocationTypeResourceLimit.is_active)
    return sorted(q, key=lambda r: (
        r.allocation_type.allocation_type if r.allocation_type else '',
        r.allocation_type.panel.panel_name if r.allocation_type and r.allocation_type.panel
        else '', r.resource.resource_name if r.resource else ''))


def _create_context():
    types = (db.session.query(AllocationType).filter(AllocationType.is_active)
             .order_by(AllocationType.allocation_type).all())
    resources = (db.session.query(Resource).filter(Resource.is_active)
                 .order_by(Resource.resource_name).all())
    return {'allocation_types': types, 'resources': resources}


@bp.route('/htmx/allocation-limits', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def htmx_allocation_limits_card():
    """The limits card body."""
    active_only = read_active_only(request.args)
    return render_template('dashboards/admin/fragments/allocation_limits_card.html',
                           limits=_limits(active_only), active_only=active_only)


register_crud(bp, CrudSpec(
    slug='allocation-limit', name='Request limit',
    model=AllocationTypeResourceLimit, id_param='allocation_type_resource_limit_id',
    context_key='limit',
    edit_schema=EditAllocationLimitForm, create_schema=CreateAllocationLimitForm,
    edit_fields=('min_amount', 'default_amount', 'max_amount', 'active'),
    create_kwargs=lambda data: {
        **{k: data[k] for k in ('allocation_type_id', 'resource_id', 'min_amount',
                                'default_amount', 'max_amount')},
        'created_by': _actor()},
    create_context=_create_context,
    triggers=modal_triggers('reloadAllocationLimitsCard'),
    edit_permission=Permission.SYSTEM_ADMIN,
    create_permission=Permission.SYSTEM_ADMIN,
    delete_permission=Permission.SYSTEM_ADMIN,
))


def _actor() -> str:
    from flask_login import current_user
    return getattr(current_user, 'username', None) or 'unknown'
