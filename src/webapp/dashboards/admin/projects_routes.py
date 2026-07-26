"""
Admin dashboard — Project management routes.

Covers: Project creation (Phase A).  Edit/allocation management (Phase B).
"""

import calendar
from datetime import datetime

from flask import render_template, request, redirect, url_for
from webapp.utils.htmx import (htmx_success, htmx_success_message,
                               handle_htmx_form_post, read_active_only)
from webapp.utils.fk_validation import FKValidationError, validate_fk_existence
from flask_login import login_required, current_user

from webapp.extensions import db
from flask import abort, current_app
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility,
    has_permission, has_permission_for_facility,
    Permission, user_facility_scope,
)
from webapp.api.access_control import (
    require_project_permission, require_allocation_permission,
    require_project_facility_permission,
    require_allocation_facility_permission,
    require_project_operator_access,
)
from webapp.utils.project_permissions import (
    can_edit_project_governance,
    can_modify_allocations,
    can_exchange_allocations,
    can_allocate_residual,
)
from sam.manage import management_transaction
from sam.accounting.allocations import InheritingAllocationException
from sam.core.groups import GidAllocation, NoAvailableGidError
from sam.schemas.forms import (
    AccessGridToggleForm, AddAllocationForm, AllocateResidualForm,
    EditAllocationForm, EditProjectForm, ExchangeAllocationForm,
    ExtendAllocationsForm, RenewAllocationsForm,
)
from sam.schemas.forms.projects import (
    AddLinkedContractForm, AddLinkedDirectoryForm, AddLinkedOrganizationForm,
    EditLinkedDirectoryForm,
)
from webapp.utils.form_handler import FlattenedFieldErrors, FormError, HtmxFormHandler

from .blueprint import bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Low-water thresholds for the GID pool indicator on the Create Project form.
# Tuned so operators get a visible warning well before the pool actually runs
# out — typical production blocks hand out thousands of GIDs, but new blocks
# require coordination with the IDMS team to arrange.
_GID_POOL_WARN_THRESHOLD = 100   # >= GREEN
_GID_POOL_DANGER_THRESHOLD = 10  # >= YELLOW; below = RED


def _gid_pool_badge(summary) -> dict:
    """Map a GidPoolSummary to badge metadata for the Create Project form.

    Returns a dict with ``label``, ``css_class``, ``icon``, and
    ``disable_submit``. Keeping the threshold logic out of the template
    avoids a tangle of Jinja ternaries.
    """
    n = summary.available
    if n == 0:
        # Distinguish "table never populated" from "all blocks exhausted" —
        # both block project creation, but the remediation differs (seed a
        # block via IDMS vs. extend an existing range).
        if summary.block_count == 0:
            label = 'No GID blocks defined'
        else:
            label = 'GID pool exhausted'
        return {
            'label': label,
            'css_class': 'bg-danger',
            'icon': 'fa-circle-exclamation',
            'disable_submit': True,
        }
    if n < _GID_POOL_DANGER_THRESHOLD:
        return {
            'label': f'Only {n} GID' + ('s' if n != 1 else '') + ' available',
            'css_class': 'bg-danger',
            'icon': 'fa-circle-exclamation',
            'disable_submit': False,
        }
    if n < _GID_POOL_WARN_THRESHOLD:
        return {
            'label': f'{n} GIDs available',
            'css_class': 'bg-warning text-dark',
            'icon': 'fa-triangle-exclamation',
            'disable_submit': False,
        }
    return {
        'label': f'{n:,} GIDs available',
        'css_class': 'bg-success',
        'icon': 'fa-check',
        'disable_submit': False,
    }

def _project_form_data(form=None) -> dict:
    """Load form option lists shared by create (and later edit) forms.

    When *form* is provided (a re-render after validation errors) the
    panel and alloc-type lists are pre-filtered so the selects repopulate
    without requiring an htmx round-trip.
    """
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Facility, Panel
    from sam.core.organizations import MnemonicCode

    areas = (
        db.session.query(AreaOfInterest)
        .filter(AreaOfInterest.is_active)
        .order_by(AreaOfInterest.area_of_interest)
        .all()
    )
    aoi_groups = (
        db.session.query(AreaOfInterestGroup)
        .order_by(AreaOfInterestGroup.name)
        .all()
    )
    facilities_q = (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
    )
    # Facility-scoped users only ever see (and can submit) facilities
    # they have CREATE_PROJECTS on. None → no restriction.
    allowed = user_facility_scope(current_user, Permission.CREATE_PROJECTS)
    if allowed is not None:
        facilities_q = facilities_q.filter(Facility.facility_name.in_(allowed))
    facilities = facilities_q.all()
    mnemonics = (
        db.session.query(MnemonicCode)
        .filter(MnemonicCode.is_active)
        .order_by(MnemonicCode.code)
        .all()
    )

    # Pre-populate dependent selects on error re-render
    panels_for_facility = []
    alloc_types_for_panel = []
    if form:
        fac_id_str = form.get('facility_id', '').strip()
        pan_id_str = form.get('panel_id', '').strip()
        if fac_id_str:
            try:
                panels_for_facility = (
                    db.session.query(Panel)
                    .filter(Panel.facility_id == int(fac_id_str), Panel.is_active)
                    .order_by(Panel.panel_name)
                    .all()
                )
            except (ValueError, TypeError):
                pass
        if pan_id_str:
            try:
                alloc_types_for_panel = (
                    db.session.query(AllocationType)
                    .filter(AllocationType.panel_id == int(pan_id_str), AllocationType.is_active)
                    .order_by(AllocationType.allocation_type)
                    .all()
                )
            except (ValueError, TypeError):
                pass

    pool_summary = GidAllocation.pool_summary(db.session)

    return dict(
        areas=areas,
        aoi_groups=aoi_groups,
        facilities=facilities,
        mnemonics=mnemonics,
        panels_for_facility=panels_for_facility,
        alloc_types_for_panel=alloc_types_for_panel,
        gid_pool_summary=pool_summary,
        gid_pool_badge=_gid_pool_badge(pool_summary),
    )


def _resources_with_allocation(project) -> set:
    """resource_ids the project already holds an allocation on (via a live account).

    Used to filter the "Add Allocation" resource dropdown. We exclude by
    *allocation*, not by *account*: an empty account (synced or member-only,
    with no allocation yet) must NOT hide its resource — the admin still needs
    to grant that first allocation. Soft-deleted accounts are also excluded
    here so their resource stays offered; ``Account.get_or_create`` revives
    such an account instead of colliding on the ``project_resource_ux`` slot.
    """
    return {
        acct.resource_id
        for acct in project.accounts
        if not acct.deleted and acct.allocations
    }


# ---------------------------------------------------------------------------
# Create Project
# ---------------------------------------------------------------------------


@bp.route('/htmx/project-create-form')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_create_form():
    """Return the project create form fragment (loaded into modal on button click)."""
    return render_template(
        'dashboards/admin/fragments/create_project_form_htmx.html',
        **_project_form_data(),
    )


@bp.route('/htmx/panels-for-facility')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_panels_for_facility():
    """Return <option> elements for the Panel select, filtered by facility.

    Called via hx-get when the Facility select changes.
    """
    from sam.resources.facilities import Facility, Panel

    facility_id_str = request.args.get('facility_id', '').strip()
    if not facility_id_str:
        return '<option value="">— Select facility first —</option>'
    try:
        facility_id_int = int(facility_id_str)
    except (ValueError, TypeError):
        return '<option value="">— Select facility first —</option>'

    # Facility-scope gate: a user with CREATE_PROJECTS only on WNA must
    # not be able to discover NCAR panels by forging facility_id. Deny
    # at the source rather than filter the returned list silently.
    facility = db.session.get(Facility, facility_id_int)
    if facility is None:
        return '<option value="">— Select facility first —</option>'
    if not has_permission_for_facility(
        current_user, Permission.CREATE_PROJECTS, facility.facility_name,
    ):
        abort(403)

    panels = (
        db.session.query(Panel)
        .filter(Panel.facility_id == facility_id_int, Panel.is_active)
        .order_by(Panel.panel_name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/panel_options_htmx.html',
        panels=panels,
        selected_id=None,
    )


@bp.route('/htmx/alloc-types-for-panel')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_alloc_types_for_panel():
    """Return <option> elements for the AllocationType select, filtered by panel.

    Called via hx-get when the Panel select changes.
    """
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Panel

    panel_id_str = request.args.get('panel_id', '').strip()
    if not panel_id_str:
        return '<option value="">— None —</option>'
    try:
        panel_id_int = int(panel_id_str)
    except (ValueError, TypeError):
        return '<option value="">— None —</option>'

    # Resolve the panel's facility for the scope check — a scoped user
    # must not be able to harvest allocation-type options from facilities
    # outside their grant by probing panel_ids directly.
    panel = db.session.get(Panel, panel_id_int)
    if panel is None:
        return '<option value="">— None —</option>'
    if not has_permission_for_facility(
        current_user, Permission.CREATE_PROJECTS,
        panel.facility.facility_name if panel.facility else None,
    ):
        abort(403)

    alloc_types = (
        db.session.query(AllocationType)
        .filter(AllocationType.panel_id == panel_id_int, AllocationType.is_active)
        .order_by(AllocationType.allocation_type)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/alloc_type_options_htmx.html',
        alloc_types=alloc_types,
        selected_id=None,
    )


@bp.route('/htmx/org-search-for-project')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_org_search_for_project():
    """Search organizations for the project create form FK picker.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``organization_id`` input.
    """
    from sam.core.organizations import Organization

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    orgs = (
        db.session.query(Organization)
        .filter(
            Organization.is_active,
            Organization.name.ilike(f'%{query}%') | Organization.acronym.ilike(f'%{query}%')
        )
        .order_by(Organization.name)
        .limit(15)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/org_search_results_fk_htmx.html',
        orgs=orgs,
    )


@bp.route('/htmx/contract-search-for-project')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_contract_search_for_project():
    """Search contracts for the project create form FK picker.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``contract_id`` input.
    """
    from sam.projects.contracts import Contract

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    contracts = (
        db.session.query(Contract)
        .filter(
            Contract.contract_number.ilike(f'%{query}%') | Contract.title.ilike(f'%{query}%')
        )
        .order_by(Contract.contract_number)
        .limit(10)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/contract_search_results_fk_htmx.html',
        contracts=contracts,
    )


@bp.route('/htmx/project-search-for-parent')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_search_for_parent():
    """Search projects for use as parent FK in the create form.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``parent_id`` input.
    """
    from sam.queries.projects import search_projects_by_code_or_title

    query = request.args.get('q', '').strip()
    if len(query) < 1:
        return ''

    projects = search_projects_by_code_or_title(
        db.session, query, active=True
    )[:10]

    return render_template(
        'dashboards/admin/fragments/project_search_results_fk_htmx.html',
        projects=projects,
    )


@bp.route('/htmx/project-projcode-preview')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_projcode_preview():
    """Preview + availability check for the Create Project code, both modes.

    auto:   ?projcode_mode=auto&facility_id=N&mnemonic_code_id=N
            → next collision-free code (side-effect-free preview of
              ``next_projcode``; the counter is only advanced at submit).
    manual: ?projcode_mode=manual&projcode=UCSD0042
            → availability of the typed code against existing projects
              AND adhoc_group names (projcodes become Unix group names).

    Returns the ``projcode_preview_htmx`` fragment: a colored badge plus an
    availability note. Incomplete input renders the neutral em-dash badge.
    """
    from sam.projects.projects import (
        next_projcode, projcode_collision, ProjcodeExhaustedError,
    )

    mode = request.args.get('projcode_mode', 'auto').strip()
    ctx = {'status': 'incomplete', 'code': None, 'detail': None}

    if mode == 'manual':
        code = request.args.get('projcode', '').strip().upper()
        if code:
            collision = projcode_collision(db.session, code)
            if collision:
                ctx.update(status='taken', code=code, detail=collision)
            else:
                ctx.update(status='available', code=code)
    else:
        facility_id_str = request.args.get('facility_id', '').strip()
        mnemonic_id_str = request.args.get('mnemonic_code_id', '').strip()
        if facility_id_str.isdigit() and mnemonic_id_str.isdigit():
            try:
                code = next_projcode(
                    db.session,
                    facility_id=int(facility_id_str),
                    mnemonic_code_id=int(mnemonic_id_str),
                )
                ctx.update(status='available', code=code)
            except (ValueError, ProjcodeExhaustedError) as exc:
                ctx.update(status='error', detail=str(exc))

    return render_template(
        'dashboards/admin/fragments/projcode_preview_htmx.html', **ctx)


@bp.route('/htmx/project-lead-hint')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_lead_hint():
    """Contextual hint shown once a Project Lead is selected.

    Surfaces the lead's current organization / institution (whichever
    exist) — these drive the conventional mnemonic choice: university
    leads get their institution's mnemonic, staff get their lab/org's.
    When an active mnemonic matches, the fragment offers a one-click
    "Use <CODE>" button; when the lead has an organization, an
    "Use as Organization" button pre-fills the Organization picker.

    Empty ``project_lead_user_id`` (fk:cleared) clears the hint.
    """
    from sam.core.users import User
    from sam.core.organizations import MnemonicCode

    uid = request.args.get('project_lead_user_id', '').strip()
    if not uid.isdigit():
        return ''
    user = db.session.get(User, int(uid))
    if not user:
        return ''

    org = next((uo.organization for uo in user.organizations if uo.is_active), None)
    institution = next((ui.institution for ui in user.institutions if ui.is_active), None)
    if not org and not institution:
        return render_template(
            'dashboards/admin/fragments/project_lead_hint_htmx.html',
            org=None, institution=None, suggestion=None)

    # Suggest a mnemonic via the existing soft-link resolvers (ports of
    # legacy Java UserOrganizationStrategy / UserInstitutionStrategy):
    # org matches on exact name, institution on "Name, City" then "Name".
    # No match → no suggestion, never a guess.
    lookup = MnemonicCode.build_lookup(db.session)
    suggested_code = None
    if org:
        suggested_code = MnemonicCode.resolve_for_organization(org, lookup)
    if not suggested_code and institution:
        suggested_code = MnemonicCode.resolve_for_institution(institution, lookup)
    suggestion = None
    if suggested_code:
        suggestion = (
            db.session.query(MnemonicCode)
            .filter(MnemonicCode.code == suggested_code)
            .first()
        )

    return render_template(
        'dashboards/admin/fragments/project_lead_hint_htmx.html',
        org=org, institution=institution, suggestion=suggestion)


@bp.route('/htmx/project-parent-prefill')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_parent_prefill():
    """Re-render the Facility/Panel/AllocType cascade with the parent's values.

    Selecting a Parent Project usually implies the child shares its
    facility, panel, and allocation type — derive all three from
    ``parent.allocation_type`` (the same reverse lookup the edit page
    uses) and return the cascade-row fragment with the selects populated
    and pre-selected. The operator can still change any of them.

    Returns 204 (htmx: no swap, row untouched) when there is nothing to
    derive: no/unknown parent, or a parent without an allocation type.
    """
    from sam.projects.projects import Project

    parent_id = request.args.get('parent_id', '').strip()
    if not parent_id.isdigit():
        return '', 204
    parent = db.session.get(Project, int(parent_id))
    if not parent or not parent.allocation_type or not parent.allocation_type.panel:
        return '', 204
    panel = parent.allocation_type.panel

    prefill = {
        'facility_id': str(panel.facility_id),
        'panel_id': str(panel.panel_id),
        'allocation_type_id': str(parent.allocation_type_id),
    }
    return render_template(
        'dashboards/admin/fragments/create_project_cascade_row_htmx.html',
        **_project_form_data(form=prefill),
        form=prefill,
    )


@bp.route('/htmx/project-org-hint')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_org_hint():
    """Mnemonic suggestion for whichever Organization is picked.

    Symmetric companion to the lead hint: once the Organization picker is
    populated (search result or the lead hint's "use as Organization"
    button), offer that org's soft-linked mnemonic via the same
    suggest-only "use <CODE>" button. Stays silent when the suggestion is
    already the selected mnemonic (nothing actionable) or when the org has
    no soft link. Empty ``organization_id`` (fk:cleared) clears the hint.
    """
    from sam.core.organizations import MnemonicCode, Organization

    org_id = request.args.get('organization_id', '').strip()
    if not org_id.isdigit():
        return ''
    org = db.session.get(Organization, int(org_id))
    if not org:
        return ''

    code = MnemonicCode.resolve_for_organization(
        org, MnemonicCode.build_lookup(db.session))
    if not code:
        return ''
    suggestion = (
        db.session.query(MnemonicCode)
        .filter(MnemonicCode.code == code)
        .first()
    )
    if not suggestion:
        return ''

    selected = request.args.get('mnemonic_code_id', '').strip()
    if selected.isdigit() and int(selected) == suggestion.mnemonic_code_id:
        return ''

    return render_template(
        'dashboards/admin/fragments/project_org_hint_htmx.html',
        suggestion=suggestion)


@bp.route('/htmx/project-create', methods=['POST'])
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_create():
    """Validate form and create a new project."""
    # Soft feature flag: when project creation is disabled the modal renders a
    # disabled submit button, but refuse here too in case a request is forged.
    if not current_app.config.get('CREATE_PROJECTS_ENABLED', True):
        abort(403)

    from sam.projects.projects import Project
    from sam.projects.areas import AreaOfInterest
    from sam.projects.contracts import Contract, ProjectContract
    from sam.core.users import User
    from sam.core.organizations import Organization, ProjectOrganization
    from sam.resources.facilities import Facility, Panel
    from sam.accounting.allocations import AllocationType
    from sam.schemas.forms import CreateProjectForm

    def _do_action(data):
        validate_fk_existence(
            db.session,
            (Facility, data['facility_id'], 'facility'),
            (Panel, data['panel_id'], 'panel'),
            (User, data['project_lead_user_id'], 'project lead'),
            (User, data.get('project_admin_user_id'), 'project admin'),
            (AreaOfInterest, data['area_of_interest_id'], 'area of interest'),
            (AllocationType, data.get('allocation_type_id'), 'allocation type'),
            (Project, data.get('parent_id'), 'parent project'),
            (Contract, data.get('contract_id'), 'contract'),
            (Organization, data.get('organization_id'), 'organization'),
        )
        # Facility-scope gate: the decorator-level CREATE_PROJECTS check
        # only knows whether the user holds the permission anywhere.
        # Scoped users must additionally be creating inside a facility
        # they have been granted. FK existence is already validated
        # above, so the lookup is guaranteed to resolve.
        chosen_facility = db.session.get(Facility, data['facility_id'])
        if not has_permission_for_facility(
            current_user, Permission.CREATE_PROJECTS, chosen_facility.facility_name,
        ):
            abort(403)
        # Resolve the projcode. Auto mode allocates server-side (advancing
        # the project_code counter inside this transaction) — the client's
        # preview string is display-only and may be stale. Manual mode
        # keeps the operator's code but must clear both namespaces:
        # existing projcodes AND adhoc_group names (projcodes become Unix
        # group names).
        from sam.projects.projects import (
            next_projcode, projcode_collision, ProjcodeExhaustedError,
        )
        if data.get('projcode_mode', 'auto') == 'auto':
            try:
                data['projcode'] = next_projcode(
                    db.session,
                    facility_id=data['facility_id'],
                    mnemonic_code_id=data['mnemonic_code_id'],
                    allocate=True,
                )
            except (ValueError, ProjcodeExhaustedError) as exc:
                raise FKValidationError(
                    [f'Could not auto-generate a project code: {exc}'])
        else:
            collision = projcode_collision(db.session, data['projcode'])
            if collision:
                raise FKValidationError(
                    [f'Project code "{data["projcode"]}" is already in use '
                     f'by {collision}.']
                )

        # Draw a Unix GID from the pool. Happens inside the outer
        # `management_transaction`, so a downstream failure in
        # `Project.create()` (or any of the linked-org/contract steps
        # below) rolls back the gid_allocation.next_gid increment too —
        # an abandoned/failed creation never consumes a GID.
        try:
            unix_gid = GidAllocation.allocate_next_gid(db.session)
        except NoAvailableGidError:
            raise FKValidationError([
                'GID pool is exhausted — no Unix GID could be allocated. '
                'Add a new gid_allocation block before creating more projects.'
            ])

        # facility_id / panel_id are existence-only; Project.create() derives
        # the effective facility/panel from allocation_type_id.
        project_kwargs = {
            k: v for k, v in data.items()
            if k not in ('facility_id', 'panel_id',
                         'contract_id', 'organization_id',
                         'projcode_mode', 'mnemonic_code_id')
        }
        project_kwargs['unix_gid'] = unix_gid
        project = Project.create(db.session, **project_kwargs)
        if data.get('contract_id'):
            ProjectContract.create(
                db.session,
                project_id=project.project_id,
                contract_id=data['contract_id'],
            )
        if data.get('organization_id'):
            ProjectOrganization.create(
                db.session,
                project_id=project.project_id,
                organization_id=data['organization_id'],
            )
        return project

    return handle_htmx_form_post(
        schema_cls=CreateProjectForm,
        template='dashboards/admin/fragments/create_project_form_htmx.html',
        context_fn=lambda: _project_form_data(form=request.form),
        success_triggers=lambda project: {
            'closeActiveModal': {},
            'loadNewProject': project.projcode,
        },
        success_message='Project created successfully.',
        success_detail=lambda project: (
            f'{project.projcode} — {project.title}  '
            f'(Unix GID: {project.unix_gid})'
        ),
        # Land on the edit page — the natural next step (add allocations,
        # members) — rather than back on the admin search card.
        success_redirect=lambda project: url_for(
            'admin_dashboard.edit_project_page', projcode=project.projcode),
        error_prefix='Error creating project',
        do_action=_do_action,
    )


# ---------------------------------------------------------------------------
# Edit Project (Phase B)
# ---------------------------------------------------------------------------

@bp.route('/project/<projcode>/edit')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def edit_project_page(project):
    """Full edit-project page (not a modal).

    Renders a three-tab page: Details | Allocations | Members.
    The Allocations tab is lazy-loaded on first click.

    Access: system EDIT_PROJECTS, or project lead, or project admin
    (``can_access_edit_project_page``). Non-admin stewards see every
    tab but a limited edit surface gated by ``can_edit_governance``.
    """
    from datetime import datetime
    from sam.queries.dashboard import get_project_dashboard_data

    project_data = get_project_dashboard_data(db.session, project.projcode)

    # Reverse-lookup facility_id / panel_id for cascading dropdown pre-population.
    current_facility_id = None
    current_panel_id = None
    if project.allocation_type and project.allocation_type.panel:
        current_panel_id = project.allocation_type.panel_id
        if project.allocation_type.panel.facility:
            current_facility_id = project.allocation_type.panel.facility_id

    # Feed the current facility_id / panel_id into _project_form_data so it
    # pre-loads the cascading Panel and Allocation Type option lists — the same
    # mechanism used by the create form on validation-error re-renders.
    pre_fill = {}
    if current_facility_id:
        pre_fill['facility_id'] = str(current_facility_id)
    if current_panel_id:
        pre_fill['panel_id'] = str(current_panel_id)
    form_data = _project_form_data(form=pre_fill or None)

    can_edit_governance = can_edit_project_governance(current_user, project)
    can_modify_allocs = can_modify_allocations(current_user, project)
    from webapp.utils.rbac import has_permission_any_facility
    can_access_admin = has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD)

    # Initial value for the Allocations tab "Active at" date picker (today).
    # ISO YYYY-MM-DD is the machine value an <input type="date"> requires, not
    # human display — mirrors the now_str line in htmx_project_allocation_tree.
    now_str = datetime.now().strftime('%Y-%m-%d')

    return render_template(
        'dashboards/admin/edit_project.html',
        project=project,
        project_data=project_data,
        current_facility_id=current_facility_id,
        current_panel_id=current_panel_id,
        can_edit_governance=can_edit_governance,
        can_modify_allocations=can_modify_allocs,
        can_access_admin=can_access_admin,
        now_str=now_str,
        **form_data,
    )


GOVERNANCE_FIELDS = frozenset({
    'facility_id', 'panel_id', 'allocation_type_id',
    'project_lead_user_id', 'project_admin_user_id',
    'active', 'charging_exempt', 'ext_alias',
})


class _ProjectUpdateHandler(HtmxFormHandler):
    """Validate and apply project metadata updates."""

    schema_cls = EditProjectForm
    template = 'dashboards/admin/fragments/edit_project_details_htmx.html'
    partial = True
    error_prefix = 'Error updating project'
    success_message = 'Project updated successfully.'

    def form_input(self):
        # Governance fields are admin-only. When a non-admin steward
        # submits, drop those keys before marshmallow sees them.
        # Defense-in-depth: the template renders them as read-only text for
        # non-admins (so browsers don't submit them), but a crafted curl
        # request could include them.
        if can_edit_project_governance(current_user, self.project):
            return request.form
        return {k: v for k, v in request.form.items()
                if k not in GOVERNANCE_FIELDS}

    def perform(self, data):
        from sam.projects.areas import AreaOfInterest
        from sam.accounting.allocations import AllocationType
        from sam.core.users import User
        validate_fk_existence(
            db.session,
            (User, data.get('project_lead_user_id'), 'project lead'),
            (User, data.get('project_admin_user_id'), 'project admin'),
            (AreaOfInterest, data.get('area_of_interest_id'), 'area of interest'),
            (AllocationType, data.get('allocation_type_id'), 'allocation type'),
        )
        self.project.update(**data)

    def context(self):
        current_facility_id = None
        current_panel_id = None
        if self.project.allocation_type and self.project.allocation_type.panel:
            current_panel_id = self.project.allocation_type.panel_id
            if self.project.allocation_type.panel.facility:
                current_facility_id = self.project.allocation_type.panel.facility_id
        return {
            'project': self.project,
            'current_facility_id': current_facility_id,
            'current_panel_id': current_panel_id,
            'can_edit_governance': can_edit_project_governance(current_user, self.project),
            **_project_form_data(form=request.form),
        }

    def triggers(self, result):
        return {'reloadEditProjectDetails': self.project.projcode}

    def detail(self, result):
        return f'{self.project.projcode} — {self.project.title}'


@bp.route('/htmx/project-update/<projcode>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_update(project):
    """Validate and apply project metadata updates.

    Access: system EDIT_PROJECTS, or project lead/admin. Non-admin
    stewards can only change metadata fields (title / abstract /
    area_of_interest_id); governance-field submissions are stripped
    server-side before validation.
    """
    return _ProjectUpdateHandler(project=project).handle()


@bp.route('/htmx/project-allocation-tree/<projcode>')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_allocation_tree(project):
    """Lazy-loaded allocation tree for the Edit Project Allocations tab.

    Builds a {projcode: {resource_name: resource_dict}} lookup for all active
    nodes in the project tree, groups resources by resource type into tabs, and
    renders accordion cards within each tab.

    Accepts an optional ?active_at=YYYY-MM-DD query parameter to show
    allocations as they were (or will be) on a given date.  Defaults to today.
    """
    from collections import OrderedDict
    from datetime import datetime
    from sam.queries.dashboard import _build_project_resources_data

    # Parse optional active_at date; default to today.
    active_at_str = request.args.get('active_at', '').strip()
    try:
        active_at = datetime.strptime(active_at_str, '%Y-%m-%d') if active_at_str else None
    except ValueError:
        active_at = None
    now_str = datetime.now().strftime('%Y-%m-%d')
    active_at_str = active_at.strftime('%Y-%m-%d') if active_at else now_str

    root = project.get_root() if hasattr(project, 'get_root') else project

    # Always show active projects only in the allocation tree.
    all_nodes = [n for n in ([root] + root.get_descendants()) if n.active]
    resources_by_projcode = {}
    for node in all_nodes:
        node_resources = _build_project_resources_data(node, active_at=active_at)
        resources_by_projcode[node.projcode] = {
            r['resource_name']: r for r in node_resources
        }

    # Build resource_type lookup from the data already loaded.
    resource_type_lookup = {}  # {resource_name: resource_type_string}
    for res_dict in resources_by_projcode.values():
        for rname, rdata in res_dict.items():
            if rname not in resource_type_lookup:
                resource_type_lookup[rname] = rdata.get('resource_type', 'HPC')

    # Group resources into display tabs (ordered; skip empty tabs).
    _TAB_CONFIG = [
        ('HPC / DAV',    ['HPC', 'DAV']),
        ('Disk',         ['DISK']),
        ('Archive',      ['ARCHIVE']),
        ('Data Access',  ['DATA ACCESS']),
    ]
    resources_by_tab = OrderedDict()
    for tab_label, rtypes in _TAB_CONFIG:
        names = sorted(
            rname for rname, rtype in resource_type_lookup.items()
            if rtype in rtypes
        )
        if names:
            resources_by_tab[tab_label] = {
                'names': names,
                'rtypes': rtypes,
                'rtypes_str': ','.join(rtypes),
            }

    # Exchange eligibility: a resource is eligible when at least two
    # distinct DESCENDANT projects (NOT the edit-page project itself)
    # hold a dedicated (non-inheriting) allocation for it. The root is
    # never a valid exchange endpoint — see ``_exchange_candidates``.
    # Computed from the data already loaded above; no extra DB trips.
    can_exchange = can_exchange_allocations(current_user, project)
    can_modify_allocs = can_modify_allocations(current_user, project)
    descendant_projcodes = {
        p.projcode for p in project.get_descendants(include_self=False)
        if p.active
    }
    exchange_eligible_resources = set()
    if can_exchange:
        per_resource_counts = {}  # resource_name → count of dedicated allocs among descendants
        for pc in descendant_projcodes:
            for rname, rdata in resources_by_projcode.get(pc, {}).items():
                if rdata.get('allocation_id') and not rdata.get('is_inheriting'):
                    per_resource_counts[rname] = per_resource_counts.get(rname, 0) + 1
        exchange_eligible_resources = {
            rname for rname, count in per_resource_counts.items() if count >= 2
        }

    # Resolve resource_id by name so the Exchange button's URL can target
    # /htmx/exchange-allocation-form/<projcode>/<resource_id>. Only needed
    # when exchange eligibility is non-empty.
    resource_id_by_name = {}
    if exchange_eligible_resources:
        from sam.resources.resources import Resource
        resource_id_by_name = {
            r.resource_name: r.resource_id
            for r in db.session.query(Resource)
            .filter(Resource.resource_name.in_(exchange_eligible_resources))
            .all()
        }

    # Carve-out residual per (parent node, dedicated resource allocation):
    # surfaced only on nodes that actually have carve-out children (pure
    # pool nodes and fully-uncovered parents keep the Add/Propagate flows).
    # Cost note: one frontier walk per parent-node allocation — this route
    # already calls get_detailed_allocation_usage per node, which is far
    # heavier; admin tree views are small and rare.
    #
    # Frontier date-filtering uses the displayed allocation row's own
    # window, so a historical/future `active_at` view stays coherent: the
    # residual shown always belongs to the row it annotates.
    from sam.manage.allocations import get_carveout_frontier
    from sam.accounting.allocations import Allocation
    for node in all_nodes:
        if not any(c.active for c in node.children):
            continue
        node_can = None   # lazy per-node permission memo
        for rname, rdata in resources_by_projcode[node.projcode].items():
            if not rdata.get('allocation_id') or rdata.get('is_inheriting'):
                continue
            alloc = db.session.get(Allocation, rdata['allocation_id'])
            if alloc is None:
                continue
            frontier = get_carveout_frontier(db.session, alloc)
            if not frontier.carve_children:
                continue
            if node_can is None:
                node_can = can_allocate_residual(current_user, node)
            rdata['carve_residual'] = {
                'residual': frontier.residual,
                'raw_residual': frontier.raw_residual,
                'carve_total': frontier.carve_total,
                'can_allocate': node_can,
                'has_targets': bool(frontier.carve_children or frontier.open_projects),
            }

    return render_template(
        'dashboards/admin/fragments/project_allocation_tree_htmx.html',
        root=root,
        projcode=project.projcode,
        resources_by_tab=resources_by_tab,
        resources_by_projcode=resources_by_projcode,
        active_at=active_at_str,
        now_str=now_str,
        can_edit_governance=can_edit_project_governance(current_user, project),
        can_modify_allocations=can_modify_allocs,
        can_exchange=can_exchange,
        exchange_eligible_resources=exchange_eligible_resources,
        resource_id_by_name=resource_id_by_name,
    )


@bp.route('/htmx/add-allocation-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_add_allocation_form(project):
    """Return the add-allocation sub-form (loaded into modal on button click)."""
    import calendar
    from sam.resources.resources import Resource

    # Resources the project already holds an allocation on (empty accounts don't count).
    linked_resource_ids = _resources_with_allocation(project)

    # Offer all active resources the project doesn't yet have an allocation on.
    available_resources = (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )
    available_resources = [r for r in available_resources
                           if r.resource_id not in linked_resource_ids]

    active_descendants = [d for d in project.get_descendants() if d.active]

    # Default end date = last day of the same month, one year out.
    # (E.g. today 2026-04-13 → default end 2027-04-30.) User can override.
    now = datetime.now()
    target_year = now.year + 1
    last_day = calendar.monthrange(target_year, now.month)[1]
    default_end_date = f'{target_year:04d}-{now.month:02d}-{last_day:02d}'

    return render_template(
        'dashboards/admin/fragments/add_allocation_form_htmx.html',
        project=project,
        available_resources=available_resources,
        today=now.strftime('%Y-%m-%d'),
        default_end_date=default_end_date,
        project_has_children=project.has_children,
        child_count=len(active_descendants),
    )


class _AddAllocationHandler(HtmxFormHandler):
    """Create a new account + allocation for the project, optionally
    propagating to active sub-projects."""

    schema_cls = AddAllocationForm
    template = 'dashboards/admin/fragments/add_allocation_form_htmx.html'
    error_prefix = 'Error creating allocation'
    success_message = 'Allocation created successfully.'

    def clean(self, data):
        # FK existence check — requires DB access, stays out of the schema.
        from sam.resources.resources import Resource
        self.resource = db.session.get(Resource, data['resource_id'])
        if not self.resource:
            raise FormError('Selected resource does not exist.')
        return data

    def perform(self, data):
        from sam.manage.allocations import (
            create_allocation, propagate_allocation_to_subprojects,
        )
        start_date = (
            datetime.combine(data['start_date'], datetime.min.time())
            if data.get('start_date') else None
        )
        parent_alloc = create_allocation(
            db.session,
            project_id=self.project.project_id,
            resource_id=self.resource.resource_id,
            amount=data.get('amount'),
            start_date=start_date,
            end_date=data.get('end_date'),
            description=data.get('description'),
            user_id=current_user.user_id,
        )
        if data.get('apply_to_subprojects', False) and self.project.has_children:
            descendants = [d for d in self.project.get_descendants() if d.active]
            return propagate_allocation_to_subprojects(
                db.session, parent_alloc, descendants,
                user_id=current_user.user_id, skip_existing=True,
            )
        return [], []

    def context(self):
        from sam.resources.resources import Resource
        linked_ids = _resources_with_allocation(self.project)
        available = [
            r for r in (db.session.query(Resource)
                        .filter(Resource.is_active)
                        .order_by(Resource.resource_name)
                        .all())
            if r.resource_id not in linked_ids
        ]
        now = datetime.now()
        last_day = calendar.monthrange(now.year + 1, now.month)[1]
        return {
            'project': self.project,
            'available_resources': available,
            'today': now.strftime('%Y-%m-%d'),
            'default_end_date': f'{now.year + 1:04d}-{now.month:02d}-{last_day:02d}',
            'project_has_children': self.project.has_children,
            'child_count': len([d for d in self.project.get_descendants() if d.active]),
        }

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, result):
        child_created, child_skipped = result
        detail = f'{self.project.projcode} — {self.resource.resource_name}'
        if child_created or child_skipped:
            detail += (
                f'. Propagated to {len(child_created)} sub-project(s)'
                + (f'; {len(child_skipped)} already had an allocation (skipped).'
                   if child_skipped else '.')
            )
        return detail


@bp.route('/htmx/add-allocation/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_add_allocation(project):
    """Create a new account + allocation for the project."""
    return _AddAllocationHandler(project=project).handle()


# ---------------------------------------------------------------------------
# Exchange allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _exchange_candidates(project, resource_id, active_at=None):
    """Return list of dedicated allocation candidates within ``project``'s
    subtree for ``resource_id``, restricted to allocations active at
    ``active_at`` (defaults to now).

    The edit-page project itself is EXCLUDED — exchange is strictly a
    rebalancing between descendants. Moving amount *to* the root would
    not change anything (descendants inherit from it); moving amount
    *from* the root would affect children whose allocations are
    independent of it. Either way, the root is not a valid endpoint.

    Each entry is a dict: {allocation_id, amount, used, projcode,
    project_id, resource_name}. Only non-inheriting, non-deleted
    allocations on accounts owned by active descendant projects AND
    active at the reference date are included. The result is sorted by
    projcode.

    Matching ``active_at`` is essential so the dropdown shows exactly the
    allocations rendered in the tree — otherwise expired/future
    allocations for the same (project, resource) create duplicate entries.
    """
    from sam.accounting.allocations import Allocation
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sqlalchemy import or_ as sa_or

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return [], None

    subtree = {
        p.project_id: p for p in project.get_descendants(include_self=False)
        if p.active
    }
    if not subtree:
        return [], resource

    check_date = active_at or datetime.now()

    rows = (
        db.session.query(Allocation, Account)
        .join(Account, Allocation.account_id == Account.account_id)
        .filter(
            Account.project_id.in_(subtree.keys()),
            Account.resource_id == resource_id,
            Account.deleted == False,  # noqa: E712
            Allocation.deleted == False,  # noqa: E712
            Allocation.parent_allocation_id.is_(None),
            Allocation.start_date <= check_date,
            sa_or(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date,
            ),
        )
        .all()
    )

    candidates = []
    for alloc, acct in rows:
        proj = subtree.get(acct.project_id)
        if proj is None:
            continue
        # Per-project 'used' for the FROM overdraft preview / server check.
        usage = proj.get_detailed_allocation_usage(
            resource_name=resource.resource_name,
            active_at=active_at,
        )
        used = usage.get(resource.resource_name, {}).get('used', 0.0) if usage else 0.0
        candidates.append({
            'allocation_id': alloc.allocation_id,
            'amount': alloc.amount,
            'used': used,
            'projcode': proj.projcode,
            'project_id': proj.project_id,
            'resource_name': resource.resource_name,
            'title': proj.title or '',
        })

    candidates.sort(key=lambda c: c['projcode'])
    return candidates, resource


@bp.route('/htmx/exchange-allocation-form/<projcode>/<int:resource_id>')
@login_required
@require_project_permission(Permission.EDIT_ALLOCATIONS)
def htmx_exchange_allocation_form(project, resource_id):
    """Render the exchange-allocation modal form for a (project-subtree, resource) pair.

    Honors the ``active_at=YYYY-MM-DD`` query parameter carried in from the
    Allocations tab's date picker — restricts candidates to allocations
    active at that date, matching what's displayed in the tree.
    """
    active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates, resource = _exchange_candidates(project, resource_id, active_at=active_at)
    if resource is None:
        return '<div class="modal-body"><div class="alert alert-warning">Resource not found.</div></div>'
    if len(candidates) < 2:
        return (
            '<div class="modal-body">'
            '<div class="alert alert-info">'
            '<i class="fas fa-info-circle"></i> '
            'Exchange requires at least two standalone sub-project allocations '
            'for this resource within the allocation tree. Shared (linked) '
            'allocations do not count.'
            '</div></div>'
        )
    return render_template(
        'dashboards/admin/fragments/exchange_allocation_form_htmx.html',
        project=project,
        resource=resource,
        candidates=candidates,
        active_at=active_at.strftime('%Y-%m-%d'),
    )


class _ExchangeAllocationHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Validate and apply an allocation exchange within the project's subtree."""

    schema_cls = ExchangeAllocationForm
    template = 'dashboards/admin/fragments/exchange_allocation_form_htmx.html'
    error_prefix = 'Error exchanging allocations'
    success_message = 'Allocation exchanged successfully.'
    exception_map = (
        (InheritingAllocationException, lambda e: str(e)),
        (ValueError, lambda e: str(e)),
    )

    def __init__(self, **entities):
        super().__init__(**entities)
        raw = request.form.get('resource_id', '').strip()
        try:
            self.resource_id = int(raw)
        except (TypeError, ValueError):
            self.resource_id = None
        self.active_at = _parse_active_at_arg(request.form.get('active_at', ''))

    def form_input(self):
        if self.resource_id is None:
            raise FormError('Resource is required.')
        return request.form

    def clean(self, data):
        # Restrict endpoints to the edit-page project's subtree — prevents
        # forged allocation IDs from outside the authorized scope.
        candidates, self.resource = _exchange_candidates(
            self.project, self.resource_id, active_at=self.active_at)
        by_id = {c['allocation_id']: c for c in candidates}
        self.from_cand = by_id.get(data['from_allocation_id'])
        self.to_cand = by_id.get(data['to_allocation_id'])
        if self.from_cand is None or self.to_cand is None:
            raise FormError(
                "Selected allocation is not in this project's allocation tree "
                "for the chosen resource.")

        # Strict overdraft: cannot push FROM remaining below zero.
        from_remaining = self.from_cand['amount'] - self.from_cand['used']
        if data['amount'] > from_remaining:
            raise FormError(
                f"Exchange amount ({data['amount']:g}) exceeds FROM remaining "
                f"balance ({from_remaining:g}).")
        return data

    def perform(self, data):
        from sam.manage.allocations import exchange_allocations
        exchange_allocations(
            db.session,
            from_allocation_id=data['from_allocation_id'],
            to_allocation_id=data['to_allocation_id'],
            amount=data['amount'],
            user_id=current_user.user_id,
        )
        return data['amount']

    def context(self):
        candidates, resource = (
            _exchange_candidates(self.project, self.resource_id,
                                 active_at=self.active_at)
            if self.resource_id else ([], None)
        )
        return {
            'project': self.project,
            'resource': resource,
            'candidates': candidates,
            'active_at': self.active_at.strftime('%Y-%m-%d'),
        }

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, amount):
        return (
            f"{self.resource.resource_name}: -{amount:g} {self.from_cand['projcode']} / "
            f"+{amount:g} {self.to_cand['projcode']}"
        )


@bp.route('/htmx/exchange-allocation/<projcode>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_ALLOCATIONS)
def htmx_exchange_allocation(project):
    """Validate and apply an allocation exchange within the project's subtree."""
    return _ExchangeAllocationHandler(project=project).handle()


# ---------------------------------------------------------------------------
# Allocate residual down (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _allocate_down_context(allocation):
    """Build the frontier + candidate lists for the allocate-down modal.

    Returns (frontier, bump_candidates, create_candidates, resource).
    Candidates mirror what ``allocate_residual_to_child`` will accept, so
    the form can only offer valid targets (the manage op re-validates —
    that membership check is the forged-ID defense).
    """
    from sam.manage.allocations import get_carveout_frontier

    frontier = get_carveout_frontier(db.session, allocation)
    bump_candidates = sorted(
        ({
            'allocation_id': a.allocation_id,
            'projcode': a.account.project.projcode,
            'title': a.account.project.title or '',
            'amount': a.amount,
        } for a in frontier.carve_children),
        key=lambda c: c['projcode'],
    )
    create_candidates = sorted(
        ({
            'project_id': p.project_id,
            'projcode': p.projcode,
            'title': p.title or '',
        } for p in frontier.open_projects),
        key=lambda c: c['projcode'],
    )
    return frontier, bump_candidates, create_candidates, allocation.account.resource


@bp.route('/htmx/allocate-down-form/<int:allocation_id>')
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_allocate_down_form(allocation):
    """Render the allocate-down modal for one parent allocation.

    Offers the parent's unallocated residual (amount − Σ carve-outs on the
    direct frontier) for assignment to a sub-project — either by increasing
    an existing carve-out or by creating a new standalone allocation on an
    uncovered branch. The parent's own amount never changes.
    """
    if allocation.deleted or allocation.is_inheriting:
        return (
            '<div class="modal-body">'
            '<div class="alert alert-info">'
            '<i class="fas fa-info-circle"></i> '
            'This is a shared allocation — it mirrors its parent and has no '
            'unallocated remainder of its own. Allocate from the parent '
            'allocation instead.'
            '</div></div>'
        )

    frontier, bump_candidates, create_candidates, resource = \
        _allocate_down_context(allocation)

    if frontier.raw_residual < 0:
        return (
            '<div class="modal-body">'
            '<div class="alert alert-warning">'
            '<i class="fas fa-exclamation-triangle"></i> '
            f'Sub-project carve-outs ({frontier.carve_total:g}) exceed this '
            f'allocation ({float(allocation.amount):g}). Resolve the deficit '
            'before allocating further — see '
            '<code>sam-admin project --audit-trees</code>.'
            '</div></div>'
        )
    if frontier.residual <= 0 or not (bump_candidates or create_candidates):
        return (
            '<div class="modal-body">'
            '<div class="alert alert-info">'
            '<i class="fas fa-info-circle"></i> '
            'Nothing to allocate: this allocation has no unallocated remainder '
            'available for its sub-projects.'
            '</div></div>'
        )

    return render_template(
        'dashboards/admin/fragments/allocate_down_form_htmx.html',
        allocation=allocation,
        parent_projcode=allocation.account.project.projcode,
        frontier=frontier,
        bump_candidates=bump_candidates,
        create_candidates=create_candidates,
        resource=resource,
    )


class _AllocateDownHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Validate and apply an allocate-down (sub-allocation) of the residual."""

    schema_cls = AllocateResidualForm
    template = 'dashboards/admin/fragments/allocate_down_form_htmx.html'
    error_prefix = 'Error allocating to sub-project'
    success_message = 'Sub-allocation applied successfully.'
    exception_map = (
        (InheritingAllocationException, lambda e: str(e)),
        (ValueError, lambda e: str(e)),
    )

    def perform(self, data):
        from sam.manage.allocations import allocate_residual_to_child
        self.amount = data['amount']
        return allocate_residual_to_child(
            db.session,
            self.allocation.allocation_id,
            current_user.user_id,
            amount=data['amount'],
            target_allocation_id=data['target_allocation_id'],
            target_project_id=data['target_project_id'],
            comment=data.get('comment'),
        )

    def context(self):
        frontier, bump_candidates, create_candidates, resource = \
            _allocate_down_context(self.allocation)
        return {
            'allocation': self.allocation,
            'parent_projcode': self.allocation.account.project.projcode,
            'frontier': frontier,
            'bump_candidates': bump_candidates,
            'create_candidates': create_candidates,
            'resource': resource,
        }

    def triggers(self, result):
        return {'closeActiveModal': {},
                'reloadAllocationTree': self.allocation.account.project.projcode}

    def detail(self, child):
        from sam.manage.allocations import get_carveout_frontier
        residual_after = get_carveout_frontier(db.session, self.allocation).residual
        return (
            f"{self.allocation.account.resource.resource_name}: "
            f"+{self.amount:g} → {child.account.project.projcode} "
            f"(unallocated remainder now {residual_after:g})"
        )


@bp.route('/htmx/allocate-down/<int:allocation_id>', methods=['POST'])
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_allocate_down(allocation):
    """Validate and apply an allocate-down (sub-allocation) of the residual."""
    return _AllocateDownHandler(allocation=allocation).handle()


# ---------------------------------------------------------------------------
# Renew allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _parse_active_at_arg(arg: str) -> datetime:
    """Parse the ?active_at=YYYY-MM-DD query arg; default to today on empty/invalid."""
    arg = (arg or '').strip()
    if arg:
        try:
            return datetime.strptime(arg, '%Y-%m-%d')
        except ValueError:
            pass
    return datetime.now()


def _snap_to_end_of_month(d):
    """Snap *d* to the nearest natural month-end.

    Admins write allocation end dates as 'end-of-month', not 'May 2nd' or
    'Jan 1st'. Computed dates from period arithmetic can land a day or
    two off a month boundary — this normalizes them:

      - day 1  →  last day of the previous month  (May 1 → Apr 30).
      - any other day → last day of the same month (Apr 15 → Apr 30,
        Oct 29 → Oct 31, Oct 31 → Oct 31 no-op).

    The day-1 case matters for Renew when an N-year source + an N-year
    shift lands exactly on the next period's first day (e.g. a Jan 1 →
    Dec 31 source shifted 2 years gives Jan 1, which should be Dec 31).
    """
    import calendar
    from datetime import timedelta
    if d.day == 1:
        return d - timedelta(days=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last_day)


def _propose_renew_dates(source_allocs):
    """Return (new_start, new_end) as 'YYYY-MM-DD' strings for the form defaults.

    Contiguous renewal: new_start = latest source end_date + 1 day;
    new_end = new_start + (source_end - source_start), snapped to the
    last day of that month. When multiple source allocations are selected,
    we anchor on the one with the latest end date and preserve its period
    length — this naturally handles the common fiscal-year case (e.g.
    Oct 1 → Sep 30 → Oct 1 → Sep 30 next year).

    Falls back to ("today", "today + 1 year, last day of month") if the set
    is empty or lacks end dates (open-ended allocations).
    """
    from datetime import timedelta

    dated = [a for a in source_allocs if a.end_date is not None]
    if dated:
        anchor = max(dated, key=lambda a: a.end_date)
        new_start = anchor.end_date + timedelta(days=1)
        period = anchor.end_date - anchor.start_date
        new_end = _snap_to_end_of_month(new_start + period)
        return new_start.strftime('%Y-%m-%d'), new_end.strftime('%Y-%m-%d')

    now = datetime.now()
    fallback = _snap_to_end_of_month(now.replace(year=now.year + 1))
    return (
        now.strftime('%Y-%m-%d'),
        fallback.strftime('%Y-%m-%d'),
    )


def _build_renew_candidates(project, source_active_at):
    """Build the per-resource candidate rows for the Renew form.

    Returns a list of dicts (one per root-project allocation active at
    ``source_active_at``), each containing display fields the template
    needs: resource name/type, amount, tree size, and the source
    allocation id for submission.
    """
    from sam.manage.renew import (
        find_source_allocations_at,
        find_renewable_descendants,
    )

    sources = find_source_allocations_at(
        db.session, project, source_active_at
    )

    candidates = []
    for src in sources:
        resource = src.account.resource
        # Count descendants that had any (inheriting OR standalone) source
        # allocation for this resource at source_active_at — these are the
        # projects renewal will create new rows on.
        child_projects = find_renewable_descendants(
            project, resource.resource_id, source_active_at
        )
        candidates.append({
            'source_alloc': src,
            'resource_id': resource.resource_id,
            'resource_name': resource.resource_name,
            'resource_type': (
                resource.resource_type.resource_type
                if resource.resource_type else ''
            ),
            'amount': src.amount,
            'start_date': src.start_date,
            'end_date': src.end_date,
            'descendant_count': len(child_projects),
        })
    candidates.sort(key=lambda c: c['resource_name'])
    return candidates


@bp.route('/htmx/renew-allocations-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_renew_allocations_form(project):
    """Return the Renew Allocations modal form fragment.

    Query params:
        active_at (YYYY-MM-DD): which allocations to renew. Defaults to today.
    """
    # Renew always operates from the root of the project tree.
    root = project.get_root() if hasattr(project, 'get_root') else project

    source_active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates = _build_renew_candidates(root, source_active_at)

    default_start, default_end = _propose_renew_dates(
        [c['source_alloc'] for c in candidates]
    )

    return render_template(
        'dashboards/admin/fragments/renew_allocations_form_htmx.html',
        project=project,
        root=root,
        candidates=candidates,
        source_active_at=source_active_at.strftime('%Y-%m-%d'),
        default_start=default_start,
        default_end=default_end,
    )


class _RenewAllocationsHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Create renewed allocations for the selected resources."""

    schema_cls = RenewAllocationsForm
    template = 'dashboards/admin/fragments/renew_allocations_form_htmx.html'
    error_prefix = 'Error renewing allocations'
    success_message = 'Allocations renewed successfully.'

    def form_input(self):
        # Collect multi-valued resource_ids + flatten scale_<rid> inputs into
        # the 'scales' dict the schema expects. Missing/blank scale entries
        # default to 1.0 inside renew_project_allocations().
        data = {k: v for k, v in request.form.items()
                if v != '' and not k.startswith('scale_')}
        data['resource_ids'] = [
            int(v) for v in request.form.getlist('resource_ids') if v
        ]
        data['scales'] = {
            int(k.removeprefix('scale_')): v
            for k, v in request.form.items()
            if k.startswith('scale_') and v.strip()
        }
        return data

    def clean(self, data):
        self.new_start = datetime.combine(data['new_start_date'], datetime.min.time())
        self.new_end = data['new_end_date']   # already datetime via post_load
        self.source_dt = datetime.combine(data['source_active_at'], datetime.min.time())

        # Pre-flight: classify each requested resource so we can produce
        # accurate error messages and (when needed) prompt the admin to set
        # replace_existing.
        from sam.manage.renew import analyze_renew_preconditions
        from sam.resources.resources import Resource
        preconditions = analyze_renew_preconditions(
            db.session,
            root_project_id=self.root.project_id,
            source_active_at=self.source_dt,
            new_start=self.new_start,
            new_end=self.new_end,
            resource_ids=data['resource_ids'],
        )
        self.resource_name = {
            r.resource_id: r.resource_name
            for r in db.session.query(Resource).filter(
                Resource.resource_id.in_(data['resource_ids'])
            )
        }
        self.no_source_ids = [rid for rid, s in preconditions.items() if s == 'no_source']
        self.overlap_ids = [rid for rid, s in preconditions.items() if s == 'overlap']
        replace_existing = data.get('replace_existing', False)

        # Bail early with a specific error when NOTHING can be renewed.
        if (not any(s == 'ok' for s in preconditions.values())
                and not (replace_existing and self.overlap_ids)):
            msgs = []
            if self.overlap_ids:
                names = self._names(self.overlap_ids)
                msgs.append(
                    f'Already has allocations overlapping '
                    f'{self.new_start.strftime("%Y-%m-%d")} → '
                    f'{self.new_end.strftime("%Y-%m-%d")}: {names}. '
                    f'Tick "Replace existing" to supersede them.'
                )
            if self.no_source_ids:
                msgs.append(
                    f'No active root allocation at '
                    f'{self.source_dt.strftime("%Y-%m-%d")} for: '
                    f'{self._names(self.no_source_ids)}.'
                )
            raise FormError(*msgs)
        return data

    def _names(self, resource_ids):
        return ', '.join(sorted(self.resource_name.get(r, f'#{r}')
                                for r in resource_ids))

    def perform(self, data):
        from sam.manage.renew import renew_project_allocations
        self.replace_existing = data.get('replace_existing', False)
        created = renew_project_allocations(
            db.session,
            root_project_id=self.root.project_id,
            source_active_at=self.source_dt,
            new_start=self.new_start,
            new_end=self.new_end,
            resource_ids=data['resource_ids'],
            scales=data.get('scales') or {},
            user_id=current_user.user_id,
            replace_existing=self.replace_existing,
        )
        if not created:
            # Defensive fallback — preconditions said 'ok' for at least one,
            # but nothing was created. Shouldn't happen but keep a sane message.
            raise FormError(
                'No allocations were renewed. Please review the form and try again.')
        return created

    def context(self):
        source_dt = datetime.combine(
            _parse_active_at_arg(request.form.get('source_active_at', '')).date(),
            datetime.min.time())
        return {
            'project': self.project,
            'root': self.root,
            'candidates': _build_renew_candidates(self.root, source_dt),
            'source_active_at': source_dt.strftime('%Y-%m-%d'),
            'default_start': request.form.get('new_start_date', ''),
            'default_end': request.form.get('new_end_date', ''),
        }

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, created):
        detail_parts = [
            f'{self.root.projcode}: renewed {len(created)} allocation(s) for '
            f'{self.new_start.strftime("%Y-%m-%d")} → '
            f'{self.new_end.strftime("%Y-%m-%d")}'
        ]
        if self.replace_existing and self.overlap_ids:
            detail_parts.append(
                f'replaced overlapping allocations for: {self._names(self.overlap_ids)}')
        if self.no_source_ids:
            detail_parts.append(
                f'skipped (no source at {self.source_dt.strftime("%Y-%m-%d")}): '
                f'{self._names(self.no_source_ids)}')
        return '; '.join(detail_parts)


@bp.route('/htmx/renew-allocations/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_renew_allocations(project):
    """Create renewed allocations for the selected resources."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    return _RenewAllocationsHandler(project=project, root=root).handle()


# ---------------------------------------------------------------------------
# Extend allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _propose_extend_end(source_allocs):
    """Return ``YYYY-MM-DD`` string: a proposed new end date for Extend.

    Anchors on the latest-ending dated source, then adds the source's
    own period length (end - start), snapped to the last day of that
    month. So a 1-year allocation proposes a 1-year push; a 6-month
    allocation proposes 6 months. Open-ended sources are ignored.
    Returns '' if no dated source is available.
    """
    dated = [a for a in source_allocs if a.end_date is not None]
    if not dated:
        return ''
    anchor = max(dated, key=lambda a: a.end_date)
    period = anchor.end_date - anchor.start_date
    return _snap_to_end_of_month(anchor.end_date + period).strftime('%Y-%m-%d')


def _build_extend_candidates(project, source_active_at):
    """Build the per-resource candidate rows for the Extend form.

    Mirrors ``_build_renew_candidates`` but emits the fields the Extend
    template needs (no start_date) and tags open-ended sources so the
    template can render them as disabled checkboxes.
    """
    from sam.manage.renew import (
        find_source_allocations_at,
        find_renewable_descendants,
    )

    sources = find_source_allocations_at(
        db.session, project, source_active_at
    )

    candidates = []
    for src in sources:
        resource = src.account.resource
        child_projects = find_renewable_descendants(
            project, resource.resource_id, source_active_at
        )
        candidates.append({
            'source_alloc': src,
            'resource_id': resource.resource_id,
            'resource_name': resource.resource_name,
            'resource_type': (
                resource.resource_type.resource_type
                if resource.resource_type else ''
            ),
            'amount': src.amount,
            'end_date': src.end_date,
            'is_open_ended': src.end_date is None,
            'descendant_count': len(child_projects),
        })
    candidates.sort(key=lambda c: c['resource_name'])
    return candidates


@bp.route('/htmx/extend-allocations-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_extend_allocations_form(project):
    """Return the Extend Allocations modal form fragment."""
    root = project.get_root() if hasattr(project, 'get_root') else project

    source_active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates = _build_extend_candidates(root, source_active_at)

    default_end = _propose_extend_end(
        [c['source_alloc'] for c in candidates if not c['is_open_ended']]
    )

    return render_template(
        'dashboards/admin/fragments/extend_allocations_form_htmx.html',
        project=project,
        root=root,
        candidates=candidates,
        source_active_at=source_active_at.strftime('%Y-%m-%d'),
        default_end=default_end,
    )


class _ExtendAllocationsHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Push end_date forward on the selected allocations."""

    schema_cls = ExtendAllocationsForm
    template = 'dashboards/admin/fragments/extend_allocations_form_htmx.html'
    error_prefix = 'Error extending allocations'
    success_message = 'Allocations extended successfully.'

    def form_input(self):
        data = {k: v for k, v in request.form.items() if v != ''}
        data['resource_ids'] = [
            int(v) for v in request.form.getlist('resource_ids') if v
        ]
        return data

    def clean(self, data):
        self.new_end = data['new_end_date']   # datetime via post_load
        self.source_dt = datetime.combine(
            data['source_active_at'], datetime.min.time())

        # Block shortening: new_end must strictly exceed every selected
        # resource's current end date at the source.
        from sam.manage.renew import find_source_alloc_at
        latest_current_end = None
        for rid in data['resource_ids']:
            src = find_source_alloc_at(self.root, rid, self.source_dt)
            if src is None or src.end_date is None:
                continue
            if latest_current_end is None or src.end_date > latest_current_end:
                latest_current_end = src.end_date
        if latest_current_end is not None and self.new_end <= latest_current_end:
            raise FormError(
                f'New end date must be later than the current latest end date '
                f'({latest_current_end.strftime("%Y-%m-%d")}).')
        return data

    def perform(self, data):
        from sam.manage.extend import extend_project_allocations
        updated = extend_project_allocations(
            db.session,
            root_project_id=self.root.project_id,
            source_active_at=self.source_dt,
            new_end=self.new_end,
            resource_ids=data['resource_ids'],
            user_id=current_user.user_id,
        )
        if not updated:
            raise FormError(
                'No allocations were extended. Either the selected resources '
                'are open-ended or already end on/after the requested date.')
        return updated

    def context(self):
        source_dt = datetime.combine(
            _parse_active_at_arg(request.form.get('source_active_at', '')).date(),
            datetime.min.time())
        return {
            'project': self.project,
            'root': self.root,
            'candidates': _build_extend_candidates(self.root, source_dt),
            'source_active_at': source_dt.strftime('%Y-%m-%d'),
            'default_end': request.form.get('new_end_date', ''),
        }

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, updated):
        return (
            f'{self.root.projcode}: extended {len(updated)} allocation(s) to '
            f'{self.new_end.strftime("%Y-%m-%d")}'
        )


@bp.route('/htmx/extend-allocations/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_extend_allocations(project):
    """Push end_date forward on the selected allocations."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    return _ExtendAllocationsHandler(project=project, root=root).handle()


@bp.route('/htmx/edit-allocation-form/<int:allocation_id>')
@login_required
@require_allocation_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation_form(allocation):
    """Return the edit-allocation form fragment (loaded into modal)."""
    from sam.manage.allocations import get_carveout_frontier, date_ranges_overlap
    from sam.accounting.accounts import Account

    projcode = allocation.account.project.projcode

    # Direct-frontier decomposition: carve-outs vs pool members among the
    # nearest allocated descendants. Drives the "Assigned to sub-projects /
    # Unallocated" strip — the same numbers the allocation tree and the
    # allocate-down modal show, so the three surfaces always agree.
    frontier = None
    if not allocation.is_inheriting:
        frontier = get_carveout_frontier(db.session, allocation)

    # Parent info for inheriting allocations
    parent_info = None
    if allocation.is_inheriting and allocation.parent:
        p = allocation.parent
        parent_proj = p.account.project if p.account else None
        parent_info = {
            'allocation_id': p.allocation_id,
            'amount': p.amount,
            'projcode': parent_proj.projcode if parent_proj and parent_proj.active else None,
        }

    # Flaw 3 fix: count descendants that have NO allocation for this resource at all
    # (not just those missing from allocation.children — detached ones are excluded)
    unlinked_descendants_count = 0
    if not allocation.is_inheriting and allocation.account:
        project = allocation.account.project
        resource_id = allocation.account.resource_id
        if project and project.has_children:
            def _has_any_alloc(proj_id):
                acct = Account.get_by_project_and_resource(db.session, proj_id, resource_id)
                return acct is not None and any(not a.deleted for a in acct.allocations)
            unlinked_descendants_count = sum(
                1 for d in project.get_descendants()
                if d.active and not _has_any_alloc(d.project_id)
            )

    # Relink candidate: standalone child allocation whose project has a parent
    # project with a compatible (overlapping, non-inheriting) allocation for the
    # same resource. When multiple candidates overlap, prefer the latest start_date.
    relink_candidate = None
    if not allocation.is_inheriting and allocation.account:
        proj = allocation.account.project
        if proj and proj.parent_id and proj.parent and proj.parent.active:
            parent_acct = Account.get_by_project_and_resource(
                db.session, proj.parent.project_id, allocation.account.resource_id
            )
            if parent_acct:
                # Parent allocation may itself be inheriting — the deep-tree
                # invariant points at the immediate project-parent's allocation,
                # not the root. A grandchild correctly links to an inheriting
                # middle-tier parent.
                candidates = [
                    a for a in parent_acct.allocations
                    if not a.deleted
                    and date_ranges_overlap(a, allocation)
                ]
                if candidates:
                    best = max(candidates, key=lambda a: a.start_date or datetime.min)
                    relink_candidate = {
                        'allocation_id': best.allocation_id,
                        'amount': best.amount,
                        'start_date': best.start_date,
                        'end_date': best.end_date,
                        'projcode': proj.parent.projcode,
                    }

    return render_template(
        'dashboards/admin/fragments/edit_allocation_form_htmx.html',
        allocation=allocation,
        projcode=projcode,
        frontier=frontier,
        parent_info=parent_info,
        unlinked_descendants_count=unlinked_descendants_count,
        relink_candidate=relink_candidate,
    )


class _EditAllocationHandler(HtmxFormHandler):
    """Validate and apply allocation edits with cascade + audit logging."""

    schema_cls = EditAllocationForm
    template = 'dashboards/admin/fragments/edit_allocation_form_htmx.html'
    partial = True
    error_prefix = 'Error updating allocation'
    success_message = 'Allocation updated successfully.'
    exception_map = (
        (InheritingAllocationException, (
            'Cannot directly edit a shared allocation. '
            'Check "I understand — permanently break inheritance and allow '
            'editing these fields" to detach it first, or edit the parent '
            'allocation — changes are applied here automatically.')),
    )

    def form_input(self):
        # Drop empty amount/start_date up front (partial load must not see
        # them at all). An empty end_date is different: the schema strips it
        # and load_default fills None, and clean()'s presence-gate on
        # request.form is what turns that None into a deliberate
        # clear-to-open-ended update.
        data = dict(request.form)
        for k in ('amount', 'start_date'):
            if data.get(k) == '':
                data.pop(k, None)
        return data

    def clean(self, data):
        # Gate updates on original form presence so unspecified fields
        # aren't overwritten and empty-string end_date correctly clears.
        updates = {}
        if request.form.get('amount'):
            updates['amount'] = data['amount']
        if request.form.get('start_date'):
            updates['start_date'] = datetime.combine(
                data['start_date'], datetime.min.time())
        if 'end_date' in request.form:
            updates['end_date'] = data.get('end_date')  # datetime or None
        if 'description' in request.form:
            updates['description'] = data.get('description')
        if not updates:
            raise FormError('No changes provided.')
        return updates

    def perform(self, updates):
        from sam.manage.allocations import update_allocation, detach_allocation
        alloc_id = self.allocation.allocation_id
        if (self.allocation.is_inheriting
                and request.form.get('break_inheritance') == 'true'):
            # DETACH then EDIT: two audit records — intentional.
            # detach_allocation() calls session.flush() so is_inheriting is
            # False in the identity map before update_allocation() runs.
            detach_allocation(db.session, alloc_id, current_user.user_id)
        update_allocation(db.session, alloc_id, current_user.user_id, **updates)

    def context(self):
        from sam.manage.allocations import get_carveout_frontier
        frontier = (get_carveout_frontier(db.session, self.allocation)
                    if not self.allocation.is_inheriting else None)
        parent_info = None
        if self.allocation.is_inheriting and self.allocation.parent:
            p = self.allocation.parent
            parent_proj = p.account.project if p.account else None
            parent_info = {
                'allocation_id': p.allocation_id,
                'amount': p.amount,
                'projcode': parent_proj.projcode if parent_proj and parent_proj.active else None,
            }
        return {
            'allocation': self.allocation,
            'projcode': self.allocation.account.project.projcode,
            'frontier': frontier,
            'parent_info': parent_info,
            'unlinked_descendants_count': 0,  # skip expensive recompute on error re-renders
            'relink_candidate': None,         # skip recompute on error re-renders
        }

    def triggers(self, result):
        return {'closeActiveModal': {},
                'reloadAllocationTree': self.allocation.account.project.projcode}


@bp.route('/htmx/edit-allocation/<int:allocation_id>', methods=['POST'])
@login_required
@require_allocation_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation(allocation):
    """Validate and apply allocation edits with cascade + audit logging."""
    return _EditAllocationHandler(allocation=allocation).handle()


@bp.route('/htmx/detach-allocation/<int:allocation_id>', methods=['POST'])
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_detach_allocation(allocation):
    """Break parent_allocation_id link without editing other fields."""
    from sam.manage.allocations import detach_allocation

    projcode = allocation.account.project.projcode
    try:
        with management_transaction(db.session):
            detach_allocation(db.session, allocation.allocation_id, current_user.user_id)
    except ValueError as e:
        return f'<div class="alert alert-danger">{e}</div>', 400
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation detached successfully.',
    )


@bp.route('/htmx/link-allocation-to-parent/<int:allocation_id>', methods=['POST'])
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_link_allocation_to_parent(allocation):
    """Re-link a standalone child allocation to its parent-project allocation."""
    from sam.manage.allocations import link_allocation_to_parent

    projcode = allocation.account.project.projcode

    try:
        parent_allocation_id = int(request.form.get('parent_allocation_id', '0'))
    except (TypeError, ValueError):
        return '<div class="alert alert-danger">Invalid parent allocation id.</div>', 400
    if parent_allocation_id <= 0:
        return '<div class="alert alert-danger">Missing parent allocation id.</div>', 400

    try:
        with management_transaction(db.session):
            link_allocation_to_parent(
                db.session, allocation.allocation_id, parent_allocation_id, current_user.user_id
            )
    except ValueError as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation re-linked to parent successfully.',
    )


@bp.route('/htmx/propagate-allocation-to-remaining/<int:allocation_id>', methods=['POST'])
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_propagate_to_remaining(allocation):
    """Create child allocations for active descendants that don't yet have one."""
    from sam.accounting.accounts import Account
    from sam.manage.allocations import propagate_allocation_to_subprojects

    if allocation.is_inheriting:
        return '<div class="alert alert-danger">Invalid allocation.</div>', 400
    project = allocation.account.project
    resource_id = allocation.account.resource_id

    # Flaw 3 fix: exclude descendants that already have ANY allocation for this resource
    # (not just those linked via allocation.children — detached ones are excluded correctly)
    def _has_any_alloc(proj_id):
        acct = Account.get_by_project_and_resource(db.session, proj_id, resource_id)
        return acct is not None and any(not a.deleted for a in acct.allocations)

    descendants = [
        d for d in project.get_descendants()
        if d.active and not _has_any_alloc(d.project_id)
    ]
    try:
        with management_transaction(db.session):
            created, skipped = propagate_allocation_to_subprojects(
                db.session, allocation, descendants,
                user_id=current_user.user_id, skip_existing=True,
            )
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 400
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': project.projcode},
        'Shared allocations created successfully.',
        detail=(f'{len(created)} sub-project(s)'
                + (f'; {len(skipped)} skipped (already had an allocation)'
                   if skipped else '')),
    )


# ---------------------------------------------------------------------------
# Linked Elements (ProjectOrganization, ProjectContract, ProjectDirectory)
# ---------------------------------------------------------------------------

_ORG_LINK_FACILITIES = {'NCAR', 'CISL', 'CSL', 'ASD'}


def _disk_roots_for_picker():
    """Return all DiskResourceRootDirectory rows except the catch-all '/',
    sorted deepest-first (more path segments first, then alphabetically).

    Deepest-first matches the longest-prefix matching used during
    decomposition and avoids surprising users who expect a more specific
    root like /glade/campaign to appear above the bare /glade in the
    dropdown.
    """
    from sam.resources.resources import DiskResourceRootDirectory
    rows = (
        db.session.query(DiskResourceRootDirectory)
        .filter(DiskResourceRootDirectory.root_directory != '/')
        .all()
    )
    rows.sort(key=lambda r: (
        -len([seg for seg in r.root_directory.strip('/').split('/') if seg]),
        r.root_directory,
    ))
    return rows


def _decompose_directory_name(directory_name, roots):
    """Split a stored ProjectDirectory.directory_name back into (root, suffix).

    Longest-prefix match wins. Returns (None, original_path) when no
    root in the supplied list is a prefix of the path.
    """
    for r in sorted(roots, key=lambda r: len(r.root_directory), reverse=True):
        base = r.root_directory.rstrip('/')
        if directory_name == r.root_directory or directory_name == base:
            return r, ''
        if directory_name.startswith(base + '/'):
            return r, directory_name[len(base) + 1:]
    return None, directory_name


def _assemble_directory_name(root, suffix):
    """Combine a root + suffix into a stored directory_name string."""
    suffix = (suffix or '').strip()
    base = root.root_directory.rstrip('/')
    return base + ('/' + suffix.lstrip('/') if suffix else '')


def _linked_elements_context(project):
    """Build the template context dict for the linked-elements fragment."""
    facility_name = None
    try:
        facility_name = project.allocation_type.panel.facility.facility_name
    except AttributeError:
        pass

    return dict(
        project=project,
        allows_org_links=(facility_name in _ORG_LINK_FACILITIES),
        active_organizations=[po for po in project.organizations if po.is_active],
        contracts=project.contracts,
        active_directories=[pd for pd in project.directories if pd.is_active],
        disk_roots=_disk_roots_for_picker(),
        can_edit_governance=can_edit_project_governance(current_user, project),
        errors=[],
    )


def _render_linked_elements(project, errors=None):
    ctx = _linked_elements_context(project)
    if errors:
        ctx['errors'] = errors
    return render_template(
        'dashboards/admin/fragments/project_linked_elements_htmx.html',
        **ctx,
    )


@bp.route('/htmx/project/<projcode>/linked-elements')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_linked_elements(project):
    """Render the linked-elements section for an edit-project page.

    Access: system EDIT_PROJECTS, or project lead/admin. Add / remove
    actions inside the fragment remain gated by the admin-only
    ``can_edit_governance`` flag.
    """
    return _render_linked_elements(project)


class _LinkedElementAddHandler(HtmxFormHandler):
    """Base for the linked-elements add handlers.

    There is no per-field form to re-render — validation and domain errors
    both land as an alert list on the whole linked-elements fragment, and
    success re-renders it fresh.
    """

    def render_errors(self, errors, field_errors=None):
        flat = [f'{field.replace("_", " ").title()}: {msg}'
                for field, msgs in (field_errors or {}).items()
                for msg in msgs]
        return _render_linked_elements(self.project, errors=list(errors) + flat)

    def on_success(self, result):
        db.session.refresh(self.project)
        return _render_linked_elements(self.project)


class _AddProjectOrganizationHandler(_LinkedElementAddHandler):
    schema_cls = AddLinkedOrganizationForm
    error_prefix = 'Error adding organization'

    def clean(self, data):
        from sam.core.organizations import Organization
        self.org = db.session.get(Organization, data['organization_id'])
        if not self.org:
            raise FormError('Organization not found.')
        # Prevent duplicate active links
        if any(po.organization_id == data['organization_id'] and po.is_active
               for po in self.project.organizations):
            raise FormError(f'"{self.org.name}" is already linked to this project.')
        return data

    def perform(self, data):
        from sam.core.organizations import ProjectOrganization
        ProjectOrganization.create(
            db.session,
            project_id=self.project.project_id,
            organization_id=data['organization_id'],
        )


@bp.route('/htmx/project/<projcode>/organizations/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_organization(projcode):
    """Link an organization to a project (NCAR facility only)."""
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    # Facility gate — caller shouldn't reach this for non-NCAR, but guard anyway
    try:
        facility_name = project.allocation_type.panel.facility.facility_name
    except AttributeError:
        facility_name = None
    if facility_name not in _ORG_LINK_FACILITIES:
        return _render_linked_elements(project, errors=['Organization links are not available for this facility.'])

    return _AddProjectOrganizationHandler(project=project).handle()


@bp.route('/htmx/project/<projcode>/organizations/<int:po_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_organization(projcode, po_id):
    """Deactivate a project-organization link (sets end_date to now)."""
    from sam.projects.projects import Project
    from sam.core.organizations import ProjectOrganization

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    po = db.session.get(ProjectOrganization, po_id)
    if not po or po.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Organization link not found.'])

    try:
        with management_transaction(db.session):
            po.deactivate()
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing organization: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


class _AddProjectContractHandler(_LinkedElementAddHandler):
    schema_cls = AddLinkedContractForm
    error_prefix = 'Error adding contract'

    def clean(self, data):
        from sam.projects.contracts import Contract
        contract = db.session.get(Contract, data['contract_id'])
        if not contract:
            raise FormError('Contract not found.')
        # Prevent duplicate links
        if any(pc.contract_id == data['contract_id']
               for pc in self.project.contracts):
            raise FormError(f'Contract "{contract.contract_number}" is already '
                            f'linked to this project.')
        return data

    def perform(self, data):
        from sam.projects.contracts import ProjectContract
        ProjectContract.create(
            db.session,
            project_id=self.project.project_id,
            contract_id=data['contract_id'],
        )


@bp.route('/htmx/project/<projcode>/contracts/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_contract(projcode):
    """Link a contract to a project."""
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    return _AddProjectContractHandler(project=project).handle()


@bp.route('/htmx/project/<projcode>/contracts/<int:pc_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_contract(projcode, pc_id):
    """Remove a project-contract link.

    If this is the sole project using the contract, also deactivates the
    Contract record (sets end_date = now).  Otherwise only removes the
    ProjectContract join row.
    """
    from sam.projects.projects import Project
    from sam.projects.contracts import ProjectContract

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    pc = db.session.get(ProjectContract, pc_id)
    if not pc or pc.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Contract link not found.'])

    contract = pc.contract
    other_links = [p for p in contract.projects if p.project_contract_id != pc_id]

    try:
        with management_transaction(db.session):
            db.session.delete(pc)
            if not other_links:
                # Sole project using this contract — deactivate the contract too
                contract.update(end_date=datetime.now())
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing contract: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


class _AddProjectDirectoryHandler(_LinkedElementAddHandler):
    schema_cls = AddLinkedDirectoryForm
    error_prefix = 'Error adding directory'

    def clean(self, data):
        from sam.resources.resources import DiskResourceRootDirectory
        root = db.session.get(DiskResourceRootDirectory, data['root_directory_id'])
        if not root or root.root_directory == '/':
            raise FormError('Selected disk root is invalid.')

        self.directory_name = _assemble_directory_name(root, data['directory_suffix'])

        # Prevent duplicate active entries
        if any(pd.directory_name == self.directory_name and pd.is_active
               for pd in self.project.directories):
            raise FormError(f'Directory "{self.directory_name}" is already '
                            f'linked to this project.')
        return data

    def perform(self, data):
        from sam.projects.projects import ProjectDirectory
        ProjectDirectory.create(
            db.session,
            project_id=self.project.project_id,
            directory_name=self.directory_name,
        )


@bp.route('/htmx/project/<projcode>/directories/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_directory(projcode):
    """Associate a filesystem directory with a project.

    Input is now (root_directory_id, directory_suffix); the route looks up
    the chosen root, rejects '/', and assembles the final directory_name.
    """
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    return _AddProjectDirectoryHandler(project=project).handle()


@bp.route('/htmx/project/<projcode>/directories/<int:pd_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_directory(projcode, pd_id):
    """Deactivate a project directory association (sets end_date to now)."""
    from sam.projects.projects import Project, ProjectDirectory

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    pd = db.session.get(ProjectDirectory, pd_id)
    if not pd or pd.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Directory not found.'])

    try:
        with management_transaction(db.session):
            pd.deactivate()
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing directory: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


# ---------------------------------------------------------------------------
# Admin: cross-project Project Directories view
# ---------------------------------------------------------------------------

_PROJECT_DIRECTORIES_RELOAD_TRIGGERS = {
    'closeActiveModal': {},
    # Both events are fired so the same admin route can refresh either
    # context: the cross-project view (#projectDirectoriesSection) or
    # the per-project linked-elements panel (#linkedElementsContainer).
    # Each handler is a no-op when its target element isn't present.
    'reloadProjectDirectoriesCard': {},
    'reloadProjectLinkedElements': {},
}


def _render_project_directories_card(*, active_only: bool):
    """Render the cross-project Project Directories card fragment.

    Groups rows by Resource via longest-prefix match of ``directory_name``
    against ``DiskResourceRootDirectory.root_directory``. Unmatched rows
    fall into a final "No Resource Identified" group.
    """
    from collections import defaultdict
    from sam.projects.projects import ProjectDirectory, Project
    from sam.resources.resources import DiskResourceRootDirectory

    roots = (
        db.session.query(DiskResourceRootDirectory)
        .order_by(DiskResourceRootDirectory.root_directory)
        .all()
    )
    # Longest prefix wins so /glade/campaign beats /glade
    roots_by_len = sorted(roots, key=lambda r: len(r.root_directory), reverse=True)

    def _resolve_resource(directory_name: str):
        for r in roots_by_len:
            if directory_name.startswith(r.root_directory):
                return r.resource
        return None

    q = db.session.query(ProjectDirectory).join(Project)
    if active_only:
        q = q.filter(ProjectDirectory.is_active)
    rows = q.order_by(ProjectDirectory.directory_name).all()

    groups = defaultdict(list)  # resource_id (or None) -> list[ProjectDirectory]
    resources_by_id = {}        # resource_id -> Resource
    for pd in rows:
        res = _resolve_resource(pd.directory_name)
        rid = res.resource_id if res is not None else None
        groups[rid].append(pd)
        if res is not None and rid not in resources_by_id:
            resources_by_id[rid] = res

    # Ordered list: real resources alphabetically, then Unmatched (None) last
    ordered_groups = sorted(
        ((rid, resources_by_id[rid], groups[rid]) for rid in resources_by_id),
        key=lambda t: t[1].resource_name.lower(),
    )
    if None in groups:
        ordered_groups.append((None, None, groups[None]))

    return render_template(
        'dashboards/admin/fragments/project_directories_card.html',
        ordered_groups=ordered_groups,
        total_rows=len(rows),
        active_only=active_only,
    )


@bp.route('/htmx/admin/project-directories')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def htmx_admin_project_directories():
    """Render the cross-project Project Directories table.

    Read-only view gated on VIEW_PROJECTS (any-facility), matching the
    sibling reference-data cards (Facilities/Orgs/Resources). The
    edit/add/deactivate controls inside the card remain gated on
    EDIT_PROJECTS/DELETE_PROJECTS in the template, so facility-scoped
    admins (e.g. WNA) see the table without the action buttons — and
    without the on-load 403 that the old system-wide EDIT_PROJECTS gate
    produced for them.
    """
    active_only = read_active_only(request.args)
    return _render_project_directories_card(active_only=active_only)


@bp.route('/htmx/admin/project-directories/new-form')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_admin_project_directory_new_form():
    """Return the create-form fragment loaded into the add modal."""
    return render_template(
        'dashboards/admin/fragments/project_directory_new_form_htmx.html',
        disk_roots=_disk_roots_for_picker(),
    )


class _DirectoryFormMixin:
    """Shared clean() for the admin project-directory create/edit modals:
    validate root + target project, assemble the final directory_name."""

    def clean(self, data):
        from sam.projects.projects import Project
        from sam.resources.resources import DiskResourceRootDirectory
        root = db.session.get(DiskResourceRootDirectory, data['root_directory_id'])
        if not root or root.root_directory == '/':
            raise FormError('Selected disk root is invalid.')
        self.target_project = db.session.get(Project, data['project_id'])
        if not self.target_project:
            raise FormError('Selected project does not exist.')
        self.directory_name = _assemble_directory_name(root, data['directory_suffix'])
        return data


class _AdminDirectoryCreateHandler(_DirectoryFormMixin, HtmxFormHandler):
    schema_cls = EditLinkedDirectoryForm
    template = 'dashboards/admin/fragments/project_directory_new_form_htmx.html'
    error_prefix = 'Error creating directory'
    success_message = 'Project directory created.'

    def clean(self, data):
        data = super().clean(data)
        if any(pd.directory_name == self.directory_name and pd.is_active
               for pd in self.target_project.directories):
            raise FormError(f'Directory "{self.directory_name}" is already '
                            f'linked to {self.target_project.projcode}.')
        return data

    def perform(self, data):
        from sam.projects.projects import ProjectDirectory
        ProjectDirectory.create(
            db.session,
            project_id=data['project_id'],
            directory_name=self.directory_name,
        )

    def context(self):
        return {'disk_roots': _disk_roots_for_picker()}

    def triggers(self, result):
        return _PROJECT_DIRECTORIES_RELOAD_TRIGGERS


@bp.route('/htmx/admin/project-directories/create', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_admin_project_directory_create():
    """Create a new project_directory row from the admin add modal.

    Input shape: (root_directory_id, directory_suffix, project_id).
    The route validates the chosen root (must exist and not equal '/'),
    assembles the final directory_name, and creates the row.
    """
    return _AdminDirectoryCreateHandler().handle()


@bp.route('/htmx/admin/project-directories/<int:pd_id>/edit-form')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_admin_project_directory_edit_form(pd_id):
    """Return the edit-form fragment loaded into the edit modal.

    Pre-populates root_directory_id + directory_suffix by decomposing the
    existing directory_name; renders an orphaned banner if no registered
    non-'/' root matches.
    """
    from sam.projects.projects import ProjectDirectory

    pd = db.session.get(ProjectDirectory, pd_id)
    if not pd:
        return '<div class="alert alert-danger">Directory not found.</div>', 404

    disk_roots = _disk_roots_for_picker()
    default_root, default_suffix = _decompose_directory_name(pd.directory_name, disk_roots)

    return render_template(
        'dashboards/admin/fragments/project_directory_edit_form_htmx.html',
        pd=pd,
        disk_roots=disk_roots,
        default_root=default_root,
        default_suffix=default_suffix,
        is_orphaned=(default_root is None),
    )


class _AdminDirectoryEditHandler(_DirectoryFormMixin, HtmxFormHandler):
    schema_cls = EditLinkedDirectoryForm
    template = 'dashboards/admin/fragments/project_directory_edit_form_htmx.html'
    error_prefix = 'Error updating directory'
    success_message = 'Project directory updated.'

    def perform(self, data):
        self.pd.update(
            directory_name=self.directory_name,
            project_id=data['project_id'],
        )

    def context(self):
        # Decompose afresh so banner state stays consistent on re-render.
        disk_roots = _disk_roots_for_picker()
        default_root, default_suffix = _decompose_directory_name(
            self.pd.directory_name, disk_roots)
        return {
            'pd': self.pd,
            'disk_roots': disk_roots,
            'default_root': default_root,
            'default_suffix': default_suffix,
            'is_orphaned': (default_root is None),
        }

    def triggers(self, result):
        return _PROJECT_DIRECTORIES_RELOAD_TRIGGERS


@bp.route('/htmx/admin/project-directories/<int:pd_id>/edit', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_admin_project_directory_edit(pd_id):
    """Update a project_directory row's directory_name and/or linked project."""
    from sam.projects.projects import ProjectDirectory

    pd = db.session.get(ProjectDirectory, pd_id)
    if not pd:
        return '<div class="alert alert-danger">Directory not found.</div>', 404

    return _AdminDirectoryEditHandler(pd=pd).handle()


@bp.route('/htmx/admin/project-directories/<int:pd_id>/deactivate', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_admin_project_directory_deactivate(pd_id):
    """Deactivate (soft-delete) a project_directory row."""
    from sam.projects.projects import ProjectDirectory

    pd = db.session.get(ProjectDirectory, pd_id)
    if not pd:
        return '<div class="alert alert-danger">Directory not found.</div>', 404

    try:
        with management_transaction(db.session):
            pd.deactivate()
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return htmx_success_message(
        {'reloadProjectDirectoriesCard': {}},
        'Project directory deactivated.',
    )


# ---------------------------------------------------------------------------
# Admin: bulk-deactivate Project Directories under a path prefix
# ---------------------------------------------------------------------------

def _project_dirs_matching_prefix(prefix: str, *, active_only: bool = True):
    """Return ProjectDirectory rows whose directory_name is `prefix`
    (with an optional trailing slash) or any descendant of it.

    The match must consume a full path segment, so `/glade/p` does NOT
    match `/glade/pp/foo`. Caller is responsible for any 'minimum length'
    sanity checks (the schema enforces >= 4 chars and != '/').
    """
    from sam.projects.projects import ProjectDirectory
    base = prefix.rstrip('/')
    q = db.session.query(ProjectDirectory).filter(
        (ProjectDirectory.directory_name == base) |
        (ProjectDirectory.directory_name.like(base + '/%'))
    )
    if active_only:
        q = q.filter(ProjectDirectory.is_active)
    return q.order_by(ProjectDirectory.directory_name).all()


@bp.route('/htmx/admin/project-directories/bulk-deactivate-form')
@login_required
@require_permission(Permission.DELETE_PROJECTS)
def htmx_admin_project_directory_bulk_deactivate_form():
    """Step 1: render the prefix-input form fragment in the bulk modal."""
    return render_template(
        'dashboards/admin/fragments/bulk_deactivate_project_directories_form_htmx.html',
    )


@bp.route('/htmx/admin/project-directories/bulk-deactivate-preview', methods=['POST'])
@login_required
@require_permission(Permission.DELETE_PROJECTS)
def htmx_admin_project_directory_bulk_deactivate_preview():
    """Step 2: show count + sample of paths that would be deactivated."""
    from marshmallow import ValidationError
    from sam.schemas.forms.projects import BulkDeactivateProjectDirectoriesForm

    try:
        form_data = BulkDeactivateProjectDirectoriesForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/bulk_deactivate_project_directories_form_htmx.html',
            errors=BulkDeactivateProjectDirectoriesForm.flatten_errors(e.messages),
            form=request.form,
        )

    matches = _project_dirs_matching_prefix(form_data['prefix'], active_only=True)
    return render_template(
        'dashboards/admin/fragments/bulk_deactivate_project_directories_preview_htmx.html',
        prefix=form_data['prefix'],
        matches=matches,
    )


@bp.route('/htmx/admin/project-directories/bulk-deactivate', methods=['POST'])
@login_required
@require_permission(Permission.DELETE_PROJECTS)
def htmx_admin_project_directory_bulk_deactivate():
    """Step 3: commit. Re-runs the query (in case data changed since
    preview) and deactivates all active matches inside one transaction."""
    from marshmallow import ValidationError
    from sam.schemas.forms.projects import BulkDeactivateProjectDirectoriesForm

    try:
        form_data = BulkDeactivateProjectDirectoriesForm().load(request.form)
    except ValidationError as e:
        # Bounce back to the step-1 form with the error.
        return render_template(
            'dashboards/admin/fragments/bulk_deactivate_project_directories_form_htmx.html',
            errors=BulkDeactivateProjectDirectoriesForm.flatten_errors(e.messages),
            form=request.form,
        )

    prefix = form_data['prefix']
    matches = _project_dirs_matching_prefix(prefix, active_only=True)

    if not matches:
        return render_template(
            'dashboards/admin/fragments/bulk_deactivate_project_directories_preview_htmx.html',
            prefix=prefix,
            matches=[],
            errors=['No active directories match — nothing to do.'],
        )

    try:
        with management_transaction(db.session):
            for pd in matches:
                pd.deactivate()
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/bulk_deactivate_project_directories_preview_htmx.html',
            prefix=prefix,
            matches=matches,
            errors=[f'Error during bulk deactivation: {e}'],
        )

    n = len(matches)
    return htmx_success_message(
        _PROJECT_DIRECTORIES_RELOAD_TRIGGERS,
        f'Deactivated {n} project director{"ies" if n != 1 else "y"} under "{prefix}".',
    )


# ---------------------------------------------------------------------------
# User / Resource Access grid (site-operator remediation)
#
# Surfaces and repairs partial-access errors: the underlying access model is a
# grid of AccountUser rows (member × resource account). SAM normally hides this,
# so when an out-of-band edit leaves a member without an account on some
# resource, only the CLI (`sam-search project … --list-users --verbose`) shows
# it. These operator-only routes render that grid and let an operator toggle a
# single cell or reconcile the whole project.
# ---------------------------------------------------------------------------

_ACCESS_GRID_TEMPLATE = 'dashboards/admin/fragments/project_access_grid_htmx.html'


def _build_access_grid_context(project, active_only: bool) -> dict:
    """Build the member × resource access grid for *project*.

    Thin adapter over the shared detector
    :meth:`Project.get_members_access_status` — the same computation that
    backs the CLI and the member-list warning indicator, so the grid can
    never disagree with them. Columns are resources with a currently-active
    allocation when ``active_only`` is True, otherwise every non-deleted
    account's resource (so expired/lapsed resources are also shown).
    """
    status = project.get_members_access_status(active_only=active_only)
    return {
        'project': project,
        'projcode': project.projcode,
        'columns': status['columns'],
        'member_rows': status['members'],
        'active_only': active_only,
    }


def _render_access_grid(project, active_only: bool, errors=None):
    """Render the access-grid card fragment, optionally with an error banner."""
    ctx = _build_access_grid_context(project, active_only)
    ctx['errors'] = errors or []
    return render_template(_ACCESS_GRID_TEMPLATE, **ctx)


def _access_grid_active_only(source) -> bool:
    """Read the Active-Only flag from a request args/form mapping.

    Thin alias for ``read_active_only`` kept because the grid's POST
    handlers read it off ``request.form`` as well as ``request.args``. The
    initial container load passes ``active_only=1`` explicitly to default
    ON, and every control hx-includes the switch so the mode rides along.
    """
    return read_active_only(source)


@bp.route('/htmx/access-grid/<projcode>')
@login_required
@require_project_operator_access
def htmx_access_grid(project):
    """Lazy-loaded operator-only User/Resource Access grid for a project."""
    return _render_access_grid(project, _access_grid_active_only(request.args))


class _AccessGridToggleHandler(HtmxFormHandler):
    """Grant or revoke one member's access to one project resource.

    Errors and success both re-render the whole grid fragment — there is
    no per-field form to attach inline errors to.
    """

    schema_cls = AccessGridToggleForm
    exception_map = ((ValueError, lambda e: str(e)),)

    def form_input(self):
        data = {k: v for k, v in request.form.items() if v != ''}
        data['grant'] = 'grant' in request.form
        return data

    def clean(self, data):
        # FK existence checks (schemas don't touch the DB).
        from sam.core.users import User
        from sam.resources.resources import Resource
        validate_fk_existence(
            db.session,
            (User, data['user_id'], 'user'),
            (Resource, data['resource_id'], 'resource'),
        )
        return data

    def perform(self, data):
        from sam.manage import (
            grant_user_resource_access, revoke_user_resource_access,
        )
        action = (grant_user_resource_access if data['grant']
                  else revoke_user_resource_access)
        action(db.session, self.project.project_id,
               data['user_id'], data['resource_id'])

    def render_errors(self, errors, field_errors=None):
        flat = [f'{field.replace("_", " ").title()}: {msg}'
                for field, msgs in (field_errors or {}).items()
                for msg in msgs]
        return _render_access_grid(self.project, self.active_only,
                                   errors=list(errors) + flat)

    def on_success(self, result):
        return _render_access_grid(self.project, self.active_only)


@bp.route('/htmx/access-grid/<projcode>/toggle', methods=['POST'])
@login_required
@require_project_operator_access
def htmx_access_grid_toggle(project):
    """Grant or revoke one member's access to one project resource."""
    return _AccessGridToggleHandler(
        project=project,
        active_only=_access_grid_active_only(request.form),
    ).handle()


@bp.route('/htmx/access-grid/<projcode>/reconcile', methods=['POST'])
@login_required
@require_project_operator_access
def htmx_access_grid_reconcile(project):
    """Give every project member access to every project resource."""
    from sam.manage import reconcile_project_access

    active_only = _access_grid_active_only(request.form)
    try:
        with management_transaction(db.session):
            reconcile_project_access(db.session, project.project_id)
    except ValueError as e:
        return _render_access_grid(project, active_only, errors=[str(e)])

    return _render_access_grid(project, active_only)
