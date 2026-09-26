"""
Admin dashboard — Project management routes.

Covers: Project creation (Phase A).  Edit/allocation management (Phase B).
"""

import calendar
from datetime import datetime

from flask import render_template, request, redirect, url_for
from webapp.utils.htmx import (htmx_success, htmx_success_message,
                               handle_htmx_form_post, read_active_only,
                               read_tab, register_typeahead)
from webapp.utils.fk_validation import FKValidationError, validate_fk_existence
from flask_login import login_required, current_user

from webapp.extensions import db
from flask import abort, current_app
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility,
    has_permission, has_permission_any_facility, has_permission_for_facility,
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
from sam.sqlcompat import ci_like
from sam.accounting.allocations import InheritingAllocationException
from sam.core.groups import GidAllocation, NoAvailableGidError
from sam.schemas.forms import (
    AccessGridToggleForm, AddAllocationsForm, AllocateResidualForm,
    EditAllocationForm, EditProjectForm, ExchangeAllocationForm,
    ExtendAllocationsForm, RenewAllocationsForm, AlignAllocationsForm,
    NotifyProjectForm,
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
    # they have CREATE_PROJECTS on. None -> no restriction.
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

    Used to disable rows in the "Add Allocations" grid. We exclude by
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


# FK pickers on the project create form. The .fk-search-result click
# handlers (defined in the form template) set the hidden organization_id /
# contract_id / parent_id inputs.

def _search_orgs_for_project(q, active_only):
    from sam.core.organizations import Organization
    return (
        db.session.query(Organization)
        .filter(
            Organization.is_active,
            ci_like(Organization.name, f'%{q}%') | ci_like(Organization.acronym, f'%{q}%')
        )
        .order_by(Organization.name)
        .limit(15)
        .all()
    )


def _search_contracts_for_project(q, active_only):
    """Contract FK picker on the project card.

    Deliberately ignores ``active_only``: this picker has no checkbox, and a
    project can legitimately be linked to a contract whose grant period has
    lapsed. The sibling ``_search_contracts`` behind /admin/contracts does
    honor it — it has the checkbox.

    No ``with_details``: the FK result template reads only ``contract_id``,
    ``contract_number`` and ``title``, so the eager loads would be waste.
    """
    from sam.projects.contracts import Contract
    return Contract.search_by_pattern(db.session, q, active_only=False,
                                      limit=10)


def _search_projects_for_parent(q, active_only):
    from sam.queries.projects import search_projects_by_code_or_title
    return search_projects_by_code_or_title(db.session, q, active=True)[:10]


register_typeahead(
    bp, rule='/htmx/org-search-for-project', endpoint='htmx_org_search_for_project',
    permission=Permission.CREATE_PROJECTS, any_facility=True,
    search=_search_orgs_for_project,
    template='dashboards/admin/fragments/org_search_results_fk_htmx.html',
    ctx_key='orgs',
)

register_typeahead(
    bp, rule='/htmx/contract-search-for-project',
    endpoint='htmx_contract_search_for_project',
    permission=Permission.CREATE_PROJECTS, any_facility=True,
    search=_search_contracts_for_project,
    template='dashboards/admin/fragments/contract_search_results_fk_htmx.html',
    ctx_key='contracts',
)

register_typeahead(
    bp, rule='/htmx/project-search-for-parent',
    endpoint='htmx_project_search_for_parent',
    permission=Permission.CREATE_PROJECTS, any_facility=True,
    search=_search_projects_for_parent,
    template='dashboards/admin/fragments/project_search_results_fk_htmx.html',
    ctx_key='projects', min_len=1,
)


@bp.route('/htmx/project-projcode-preview')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_projcode_preview():
    """Preview + availability check for the Create Project code, both modes.

    auto:   ?projcode_mode=auto&facility_id=N&mnemonic_code_id=N
            -> next collision-free code (side-effect-free preview of
              ``next_projcode``; the counter is only advanced at submit).
    manual: ?projcode_mode=manual&projcode=UCSD0042
            -> availability of the typed code against existing projects
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

    # The same "first current affiliation" selectors the XRAS push uses, so the
    # suggested mnemonic and what XRAS mints resolve from the identical row.
    from sam.xras.extractors import _best_institution, _best_organization
    org = _best_organization(user)
    institution = _best_institution(user)
    if not org and not institution:
        return render_template(
            'dashboards/admin/fragments/project_lead_hint_htmx.html',
            org=None, institution=None, suggestion=None)

    # Suggest a mnemonic via the existing soft-link resolvers (ports of
    # legacy Java UserOrganizationStrategy / UserInstitutionStrategy):
    # org matches on exact name, institution on "Name, City" then "Name".
    # No match -> no suggestion, never a guess.
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
@require_project_permission(Permission.EDIT_PROJECTS, include_ancestors=True)
def edit_project_page(project):
    """Full edit-project page (not a modal).

    Renders a three-tab page: Details | Allocations | Members.
    The Allocations tab is lazy-loaded on first click.

    Access: system EDIT_PROJECTS, or project lead, or project admin
    (``can_access_edit_project_page``). Non-admin stewards see every
    tab but a limited edit surface gated by ``can_edit_governance``.
    """
    from datetime import datetime

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
    can_access_admin = has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD)

    # Initial value for the Allocations tab "Active at" date picker (today).
    # ISO YYYY-MM-DD is the machine value an <input type="date"> requires, not
    # human display — mirrors the now_str line in htmx_project_allocation_tree.
    now_str = datetime.now().strftime('%Y-%m-%d')

    # Shareable point-in-time link: ?active_at=YYYY-MM-DD seeds the picker and
    # initial tree fetch (e.g. a PI link to the FY27 allocation) and implies the
    # Allocations tab; explicit ?tab= wins. _parse_active_at_arg matches Renew.
    active_at_raw = request.args.get('active_at', '').strip()
    active_at_seed = (_parse_active_at_arg(active_at_raw).strftime('%Y-%m-%d')
                      if active_at_raw else now_str)
    active_tab = read_tab('tab', {'details', 'allocations', 'members', 'invitations'},
                          'allocations' if active_at_raw else 'details')

    return render_template(
        'dashboards/admin/edit_project.html',
        project=project,
        current_facility_id=current_facility_id,
        current_panel_id=current_panel_id,
        can_edit_governance=can_edit_governance,
        can_modify_allocations=can_modify_allocs,
        can_access_admin=can_access_admin,
        now_str=now_str,
        active_at_seed=active_at_seed,
        active_tab=active_tab,
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
        # Governance checkboxes: a partial load drops an unchecked box, so read
        # the boxes from request.form. active carries an inactivate_time side
        # effect and is applied via reactivate()/deactivate(), never update().
        can_governance = can_edit_project_governance(current_user, self.project)
        data.pop('active', None)
        if can_governance:
            data['charging_exempt'] = 'charging_exempt' in request.form
        self.project.update(**data)
        if can_governance:
            want_active = 'active' in request.form
            if want_active and not self.project.active:
                self.project.reactivate()
            elif not want_active and self.project.active:
                self.project.deactivate()

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
@require_project_permission(Permission.EDIT_PROJECTS, include_ancestors=True)
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
@require_project_permission(Permission.EDIT_PROJECTS, include_ancestors=True)
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
    from sam.queries.dashboard import _build_user_projects_resources_batched

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
    # One batched build for the whole tree (the per-node loop was the ~5.7 s
    # path); the batched builder also consults the read-model when fresh.
    by_project = _build_user_projects_resources_batched(
        db.session, all_nodes, active_at=active_at,
    )
    resources_by_projcode = {
        node.projcode: {r['resource_name']: r for r in by_project.get(node.project_id, [])}
        for node in all_nodes
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
        n.projcode for n in all_nodes
        if project.tree_left < n.tree_left < project.tree_right
    }
    exchange_eligible_resources = set()
    if can_exchange:
        per_resource_counts = {}  # resource_name -> count of dedicated allocs among descendants
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
    # The walks read `node.children` and one account per visited node, so
    # both are loaded for the whole tree first (four statements) instead of
    # one lazy query per node per parent allocation.
    #
    # Frontier date-filtering uses the displayed allocation row's own
    # window, so a historical/future `active_at` view stays coherent: the
    # residual shown always belongs to the row it annotates.
    from sqlalchemy.orm import selectinload
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    from sam.manage.allocations import get_carveout_frontier
    from sam.projects.projects import Project
    node_ids = [n.project_id for n in all_nodes]
    db.session.query(Project).options(selectinload(Project.children)).filter(
        Project.project_id.in_(node_ids)).all()
    account_index = {
        (a.project_id, a.resource_id): a
        for a in db.session.query(Account).options(selectinload(Account.allocations))
        .filter(Account.project_id.in_(node_ids), Account.deleted == False)  # noqa: E712
        .order_by(Account.account_id.desc())
    }
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
            frontier = get_carveout_frontier(db.session, alloc, account_index=account_index)
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

    # Grace-window warning: every displayed root allocation expired (shown only
    # for the customary 90-day post-expiry window), so the view is not active.
    grace_window_end = _grace_window_end(
        list(resources_by_projcode.get(root.projcode, {}).values()))

    return render_template(
        'dashboards/admin/fragments/project_allocation_tree_htmx.html',
        root=root,
        projcode=project.projcode,
        resources_by_tab=resources_by_tab,
        resources_by_projcode=resources_by_projcode,
        active_at=active_at_str,
        now_str=now_str,
        grace_window_end=grace_window_end,
        can_edit_governance=can_edit_project_governance(current_user, project),
        can_modify_allocations=can_modify_allocs,
        can_exchange=can_exchange,
        exchange_eligible_resources=exchange_eligible_resources,
        resource_id_by_name=resource_id_by_name,
    )


# Resources a new project usually gets; every other active resource sits behind
# the Add Allocations grid's "Show everything" switch. Names, not ids.
COMMON_ALLOCATION_RESOURCES = (
    'Derecho', 'Derecho GPU', 'Casper', 'Casper GPU',
    'Campaign_Store', 'Destor', 'Data_Access',
)
_RESOURCE_TYPE_ORDER = ('HPC', 'DAV', 'DISK', 'ARCHIVE', 'DATA ACCESS')


def _existing_allocation_ends(project) -> dict:
    """``{resource_id: latest end_date or None (open)}`` for resources already held."""
    held = _resources_with_allocation(project)
    ends = {}
    for acct in project.accounts:
        if acct.deleted or acct.resource_id not in held:
            continue
        dates = [a.end_date for a in (acct.live_allocations or acct.allocations)]
        ends[acct.resource_id] = None if None in dates else max(dates)
    return ends


def _add_allocation_context(project) -> dict:
    """Template context for the Add Allocations grid (GET and error re-render)."""
    from sam.enums import ResourceTypeName
    from sam.resources.resources import Resource

    existing = _existing_allocation_ends(project)
    resources = (project.session.query(Resource)
                 .filter(Resource.is_active)
                 .order_by(Resource.resource_name)
                 .all())
    groups = {}
    for r in resources:
        rtype = r.resource_type.resource_type if r.resource_type else 'Other'
        held = r.resource_id in existing
        groups.setdefault(rtype, []).append({
            'resource': r,
            'held': held,
            'existing_end': existing.get(r.resource_id),
            'optional': not held and r.resource_name not in COMMON_ALLOCATION_RESOURCES,
        })
    rank = {name: i for i, name in enumerate(_RESOURCE_TYPE_ORDER)}
    resource_groups = [
        {'type_name': rtype,
         'unit': ResourceTypeName.allocation_unit(rtype),
         'rows': rows,
         'optional': all(row['optional'] for row in rows)}
        for rtype, rows in sorted(groups.items(),
                                  key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))
    ]
    all_rows = [row for g in resource_groups for row in g['rows']]
    optional_count = sum(row['optional'] for row in all_rows)

    now = datetime.now()
    last_day = calendar.monthrange(now.year + 1, now.month)[1]
    return {
        'project': project,
        'resource_groups': resource_groups,
        'any_available': any(not row['held'] for row in all_rows),
        'optional_count': optional_count,
        # A stale COMMON list must not leave an empty grid: show everything.
        'show_all_default': optional_count == len(all_rows),
        'today': now.strftime('%Y-%m-%d'),
        # Last day of the same month, one year out (2026-04-13 -> 2027-04-30).
        'default_end_date': f'{now.year + 1:04d}-{now.month:02d}-{last_day:02d}',
        'project_has_children': project.has_children,
        'child_count': len([d for d in project.get_descendants() if d.active]),
    }


@bp.route('/htmx/add-allocation-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_add_allocation_form(project):
    """Return the Add Allocations grid (loaded into modal on button click)."""
    return render_template(
        'dashboards/admin/fragments/add_allocation_form_htmx.html',
        **_add_allocation_context(project),
    )


class _AddAllocationsHandler(HtmxFormHandler):
    """Create an account + allocation for each resource given an amount, all
    over one date range, optionally propagating each to active sub-projects."""

    schema_cls = AddAllocationsForm
    template = 'dashboards/admin/fragments/add_allocation_form_htmx.html'
    error_prefix = 'Error creating allocations'
    success_message = 'Allocations created successfully.'

    def form_input(self):
        data = {k: v for k, v in request.form.items() if not k.startswith('amount_')}
        amounts = {k.removeprefix('amount_'): v for k, v in request.form.items()
                   if k.startswith('amount_') and v.strip()}
        if amounts:
            data['amounts'] = amounts
        return data

    def clean(self, data):
        from sam.resources.resources import Resource
        ids = list(data['amounts'])
        found = {r.resource_id: r for r in
                 db.session.query(Resource).filter(Resource.resource_id.in_(ids))}
        if any(rid not in found or not found[rid].is_active for rid in ids):
            raise FormError('Selected resource does not exist.')
        held = _resources_with_allocation(self.project) & set(ids)
        if held:
            names = ', '.join(sorted(found[rid].resource_name for rid in held))
            raise FormError(f'{self.project.projcode} already has an allocation on {names}.')
        self.resources = sorted(found.values(), key=lambda r: r.resource_name)
        data['amounts'] = {r.resource_id: data['amounts'][r.resource_id]
                           for r in self.resources}
        return data

    def perform(self, data):
        from sam.manage.allocations import create_allocations
        propagate_to = ()
        if data.get('apply_to_subprojects') and self.project.has_children:
            propagate_to = [d for d in self.project.get_descendants() if d.active]
        return create_allocations(
            db.session,
            project_id=self.project.project_id,
            amounts=data['amounts'],
            start_date=datetime.combine(data['start_date'], datetime.min.time()),
            end_date=data.get('end_date'),
            description=data.get('description'),
            user_id=current_user.user_id,
            propagate_to=propagate_to,
        )

    def render_errors(self, errors, field_errors=None):
        # Amount inputs have no form_fields macro; name the resource in the panel.
        from sam.resources.resources import Resource
        field_errors = dict(field_errors or {})
        amount_errors = field_errors.pop('amounts', [])
        errors = list(errors)
        for entry in amount_errors:
            if isinstance(entry, str):
                errors.append(entry)
                continue
            for rid, detail in entry.items():
                resource = (db.session.get(Resource, int(rid))
                            if str(rid).isdigit() else None)
                label = resource.resource_name if resource else f'Resource {rid}'
                errors.extend(f'{label}: {m}' for msgs in detail.values() for m in msgs)
        return super().render_errors(errors, field_errors)

    def context(self):
        return _add_allocation_context(self.project)

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, result):
        _created, child_created, child_skipped = result
        detail = (f'{self.project.projcode} — '
                  + ', '.join(r.resource_name for r in self.resources))
        if child_created or child_skipped:
            detail += (
                f'. Propagated {len(child_created)} sub-project allocation(s)'
                + (f'; {len(child_skipped)} already existed (skipped).'
                   if child_skipped else '.')
            )
        return detail


@bp.route('/htmx/add-allocation/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_add_allocation(project):
    """Create allocations on one or more resources for the project."""
    return _AddAllocationsHandler(project=project).handle()


# ---------------------------------------------------------------------------
# Exchange allocations (Edit Project -> Allocations tab)
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
            '<i class="fa-solid fa-circle-info"></i> '
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
# Allocate residual down (Edit Project -> Allocations tab)
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
            '<i class="fa-solid fa-circle-info"></i> '
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
            '<i class="fa-solid fa-triangle-exclamation"></i> '
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
            '<i class="fa-solid fa-circle-info"></i> '
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
# Renew allocations (Edit Project -> Allocations tab)
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


def _grace_window_end(alloc_dicts):
    """Most-recent end_date when every shown allocation is expired (in the
    90-day post-expiry window), else None — drives the grace-window warning.

    bar_state=='expired' means end_date < active_at (see DashboardResource);
    display_allocation only returns an expired row within 90 days, so an
    all-expired set is a view existing solely on that customary window.
    """
    shown = [d for d in alloc_dicts if d.get('allocation_id')]
    if shown and all(d.get('bar_state') == 'expired' for d in shown):
        ends = [d['end_date'] for d in shown if d.get('end_date')]
        return max(ends) if ends else None
    return None


def _snap_to_end_of_month(d):
    """Snap *d* to the nearest natural month-end.

    Admins write allocation end dates as 'end-of-month', not 'May 2nd' or
    'Jan 1st'. Computed dates from period arithmetic can land a day or
    two off a month boundary — this normalizes them:

      - day 1  ->  last day of the previous month  (May 1 -> Apr 30).
      - any other day -> last day of the same month (Apr 15 -> Apr 30,
        Oct 29 -> Oct 31, Oct 31 -> Oct 31 no-op).

    The day-1 case matters for Renew when an N-year source + an N-year
    shift lands exactly on the next period's first day (e.g. a Jan 1 ->
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
    Oct 1 -> Sep 30 -> Oct 1 -> Sep 30 next year).

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


def _build_alloc_candidates(project, source_active_at):
    """Per-resource candidate rows for the Renew/Extend forms (Extend renders
    open-ended sources as disabled checkboxes).

    One row per root allocation active at ``source_active_at``, then one per
    resource only sub-projects hold (``child_only``; its anchors are the
    topmost holders). Sorted by resource name.
    """
    from sam.manage.renew import (
        find_child_only_resources,
        find_renewable_descendants,
        find_source_allocations_at,
    )

    def row(src, anchors, child_only):
        resource = src.account.resource
        return {
            'source_alloc': src,
            'resource_id': resource.resource_id,
            'resource_name': resource.resource_name,
            'amount': src.amount if len(anchors) == 1 else None,
            'start_date': src.start_date,
            'end_date': src.end_date,
            'is_open_ended': src.end_date is None,
            # Descendants under the anchors that renew will create rows on.
            'descendant_count': sum(
                len(find_renewable_descendants(p, resource.resource_id, source_active_at))
                for p in anchors),
            'child_only': child_only,
            'anchor_projcodes': [p.projcode for p in anchors] if child_only else [],
        }

    candidates = [
        row(src, [project], False)
        for src in find_source_allocations_at(db.session, project, source_active_at)
    ]
    candidates += [
        row(anchors[0][1], [p for p, _ in anchors], True)
        for anchors in find_child_only_resources(project, source_active_at).values()
    ]
    candidates.sort(key=lambda c: c['resource_name'])
    return candidates


def _proposal_sources(candidates):
    """Source allocations to propose dates from: the root's when it has any.

    A sub-project-only source can run a partial period (e.g. Feb -> Sep);
    letting it anchor the proposal would shift the whole modal's defaults.
    """
    root = [c for c in candidates if not c['child_only']]
    return [c['source_alloc'] for c in (root or candidates)]


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
    candidates = _build_alloc_candidates(root, source_active_at)

    default_start, default_end = _propose_renew_dates(
        _proposal_sources(candidates))

    overlap = _renew_overlap(
        root, source_active_at, default_start, default_end,
        [c['resource_id'] for c in candidates])

    return render_template(
        'dashboards/admin/fragments/renew_allocations_form_htmx.html',
        project=project,
        root=root,
        candidates=candidates,
        source_active_at=source_active_at.strftime('%Y-%m-%d'),
        default_start=default_start,
        default_end=default_end,
        overlap=overlap,
    )


def _renew_overlap(root, source_active_at, new_start_str, new_end_str, resource_ids):
    """Overlap census for the Renew modal's Truncate control, or None when the
    inputs are incomplete/unparseable (e.g. a half-typed date). ``new_end`` is
    normalized to end-of-day to match the POST path's ``normalize_end_date``.
    """
    from sam.manage.renew import analyze_renew_overlap
    try:
        new_start = datetime.strptime(new_start_str, '%Y-%m-%d')
        new_end = datetime.strptime(new_end_str, '%Y-%m-%d').replace(
            hour=23, minute=59, second=59)
        rids = [int(v) for v in resource_ids if str(v).strip()]
    except (TypeError, ValueError):
        return None
    if not rids:
        return None
    return analyze_renew_overlap(
        db.session, root_project_id=root.project_id,
        source_active_at=source_active_at, new_start=new_start,
        new_end=new_end, resource_ids=rids)


@bp.route('/htmx/renew-truncate-control/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_renew_truncate_control(project):
    """Contextual "Truncate existing" control for the Renew modal.

    Recomputed live as the operator edits the proposed dates or resource
    selection: hidden when nothing overlaps, checked when truncation is
    coverage-preserving, unchecked with a warning on a collision (the period
    is already covered) or when truncating would shrink coverage past the
    new end.
    """
    root = project.get_root() if hasattr(project, 'get_root') else project
    source_active_at = _parse_active_at_arg(request.args.get('source_active_at', ''))
    overlap = _renew_overlap(
        root, source_active_at,
        request.args.get('new_start_date', ''),
        request.args.get('new_end_date', ''),
        request.args.getlist('resource_ids'))
    return render_template(
        'dashboards/admin/fragments/renew_truncate_control_htmx.html',
        overlap=overlap)


def _renewal_messages(root, *, action, new_end, active_at, touched, comment=None):
    """The renew/extend notices; ``touched`` is the write's rows or a plan's."""
    from sam.queries.lifecycle_notices import build_renewal_messages
    from webapp.utils.notify import public_url_for, public_url_root
    stamp = active_at.date().isoformat()
    return build_renewal_messages(
        db.session, root, action=action, new_end=new_end,
        touched_allocations=touched,
        operator_comment=comment,
        site_url=public_url_root(),
        requested_by=current_user.username,
        url_builder=lambda pc: public_url_for(
            'admin_dashboard.edit_project_page', projcode=pc,
            active_at=stamp, tab='allocations'),
    )


def _maybe_notify_renewal(root, *, action, new_end, active_at, touched,
                          notify_leads, comment=None):
    """Optionally mail each project lead/admin about a renew/extend.

    Post-commit and best-effort: a mail failure must never fail (or roll back)
    the allocation write. Returns a `notify_summary` dict, or None when the
    operator left the box unchecked or nothing could be sent.
    """
    if not notify_leads:
        return None
    try:
        from webapp.utils.notify import get_notifier, notify_summary
        messages = _renewal_messages(root, action=action, new_end=new_end,
                                     active_at=active_at, touched=touched,
                                     comment=comment)
        return notify_summary(get_notifier().send_many(messages))
    except Exception:  # noqa: BLE001 — never let a mail failure fail the write
        current_app.logger.exception(
            'renewal notice send failed for %s', root.projcode)
        return None


def _notified_suffix(summary) -> str:
    """`'; notified N recipient(s)'` for the success line, or '' when none."""
    if not summary:
        return ''
    n = len(summary['delivered'])
    return f'; notified {n} recipient{"" if n == 1 else "s"}' if n else ''


def _renew_form_input():
    """The Renew form as its schema wants it, for the save and the preview.

    Collects multi-valued resource_ids and flattens scale_<rid> inputs into
    the 'scales' dict; missing/blank scales default to 1.0 in the write.
    """
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
    # Default-ON checkbox: absent means unchecked, so read presence.
    data['notify_leads'] = 'notify_leads' in request.form
    return data


def _extend_form_input():
    """The Extend form as its schema wants it, for the save and the preview."""
    data = {k: v for k, v in request.form.items() if v != ''}
    data['resource_ids'] = [
        int(v) for v in request.form.getlist('resource_ids') if v
    ]
    data['notify_leads'] = 'notify_leads' in request.form
    return data


def _resource_names(names, resource_ids):
    return ', '.join(sorted(names.get(r, f'#{r}') for r in resource_ids))


def _renew_preconditions(root, source_dt, new_start, new_end, resource_ids,
                         replace_existing):
    """Renew's pre-flight, shared with its preview: which resources have no
    source or already overlap, and the errors when NOTHING can be renewed."""
    from sam.manage.renew import analyze_renew_preconditions
    from sam.resources.resources import Resource
    preconditions = analyze_renew_preconditions(
        db.session, root_project_id=root.project_id, source_active_at=source_dt,
        new_start=new_start, new_end=new_end, resource_ids=resource_ids)
    names = {r.resource_id: r.resource_name for r in
             db.session.query(Resource).filter(Resource.resource_id.in_(resource_ids))}
    no_source = [rid for rid, s in preconditions.items() if s == 'no_source']
    overlap = [rid for rid, s in preconditions.items() if s == 'overlap']
    errors = []
    if (not any(s == 'ok' for s in preconditions.values())
            and not (replace_existing and overlap)):
        if overlap:
            errors.append(
                f'Already has allocations overlapping '
                f'{new_start.strftime("%Y-%m-%d")} → {new_end.strftime("%Y-%m-%d")}: '
                f'{_resource_names(names, overlap)}. '
                f'Tick "Truncate existing" to supersede them.')
        if no_source:
            errors.append(
                f'No active allocation anywhere in the tree at '
                f'{source_dt.strftime("%Y-%m-%d")} for: '
                f'{_resource_names(names, no_source)}.')
    return {'names': names, 'no_source': no_source, 'overlap': overlap,
            'errors': errors}


def _extend_shortening_error(root, resource_ids, source_dt, new_end):
    """Extend's refusal, shared with its preview: new_end must strictly exceed
    every selected resource's current end date at the source."""
    from sam.manage.renew import find_renew_anchors
    latest_current_end = None
    for rid in resource_ids:
        for _, src in find_renew_anchors(root, rid, source_dt):
            if src.end_date is None:
                continue
            if latest_current_end is None or src.end_date > latest_current_end:
                latest_current_end = src.end_date
    if latest_current_end is not None and new_end <= latest_current_end:
        return (f'New end date must be later than the current latest end date '
                f'({latest_current_end.strftime("%Y-%m-%d")}).')
    return None


def _renewal_preview(project, root, *, action, schema_cls, form_input, plan, active_at,
                     endpoint, prefix, refusal=None):
    """The renew/extend preview pane: plan the write, then build its notices.

    Read-only: ``plan`` walks the same steps the write does and creates nothing.
    ``refusal`` returns the error the save would raise, if any.
    """
    from marshmallow import ValidationError
    from webapp.utils.email_preview import render_email_preview, render_preview_info
    try:
        data = schema_cls().load(form_input())
    except ValidationError:
        return render_preview_info('Set the dates and pick at least one resource to '
                                   'preview the email.')
    source_dt = datetime.combine(data['source_active_at'], datetime.min.time())
    refused = refusal(data, source_dt) if refusal else None
    if refused:
        return render_preview_info(refused, 'warning')
    planned = plan(data, source_dt)
    if not planned:
        return render_preview_info(f'Nothing would be {action}, so no email is sent.')
    messages = _renewal_messages(root, action=action, new_end=data['new_end_date'],
                                 active_at=active_at(data, source_dt), touched=planned,
                                 comment=data.get('operator_comment'))
    if not messages:
        return render_preview_info('No lead or admin email is on file for the '
                                   'projects this would change.')
    notes = ([] if data.get('notify_leads') else
             ['The box is unticked: saving sends no email.'])
    return render_email_preview(
        messages, id_prefix=prefix, pane_id=f'{prefix}Pane', notes=notes,
        picker_url=url_for(endpoint, projcode=project.projcode))


class _RenewAllocationsHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Create renewed allocations for the selected resources."""

    schema_cls = RenewAllocationsForm
    template = 'dashboards/admin/fragments/renew_allocations_form_htmx.html'
    error_prefix = 'Error renewing allocations'
    success_message = 'Allocations renewed successfully.'

    def form_input(self):
        return _renew_form_input()

    def clean(self, data):
        self.new_start = datetime.combine(data['new_start_date'], datetime.min.time())
        self.new_end = data['new_end_date']   # already datetime via post_load
        self.source_dt = datetime.combine(data['source_active_at'], datetime.min.time())

        pre = _renew_preconditions(self.root, self.source_dt, self.new_start,
                                   self.new_end, data['resource_ids'],
                                   data.get('replace_existing', False))
        self.resource_name = pre['names']
        self.no_source_ids, self.overlap_ids = pre['no_source'], pre['overlap']
        if pre['errors']:
            raise FormError(*pre['errors'])
        return data

    def _names(self, resource_ids):
        return _resource_names(self.resource_name, resource_ids)

    def perform(self, data):
        from sam.manage.renew import renew_project_allocations
        self.replace_existing = data.get('replace_existing', False)
        self.notify_leads = data.get('notify_leads', False)
        self.operator_comment = data.get('operator_comment')
        self.touched = []
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
            touched=self.touched,
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
            'candidates': _build_alloc_candidates(self.root, source_dt),
            'source_active_at': source_dt.strftime('%Y-%m-%d'),
            'default_start': request.form.get('new_start_date', ''),
            'default_end': request.form.get('new_end_date', ''),
            'overlap': _renew_overlap(
                self.root, source_dt,
                request.form.get('new_start_date', ''),
                request.form.get('new_end_date', ''),
                request.form.getlist('resource_ids')),
        }

    def after_commit(self, created):
        # New allocations start at new_start; land the PI there so the deep
        # link opens on what was just created.
        self._notified = _maybe_notify_renewal(
            self.root, action='renewed', new_end=self.new_end,
            active_at=self.new_start, touched=self.touched,
            notify_leads=self.notify_leads, comment=self.operator_comment)

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, created):
        detail_parts = [
            f'{self.root.projcode}: renewed {len(created)} allocation(s) for '
            f'{self.new_start.strftime("%Y-%m-%d")} → '
            f'{self.new_end.strftime("%Y-%m-%d")}'
        ]
        if self.overlap_ids:
            # A skipped overlap is the idempotent path; say so, or a mixed
            # tree reports one renewal and hides the rest.
            verb = 'superseded' if self.replace_existing else 'skipped (already covered)'
            detail_parts.append(
                f'{verb} overlapping allocations for: {self._names(self.overlap_ids)}')
        if self.no_source_ids:
            detail_parts.append(
                f'skipped (no source at {self.source_dt.strftime("%Y-%m-%d")}): '
                f'{self._names(self.no_source_ids)}')
        return '; '.join(detail_parts) + _notified_suffix(
            getattr(self, '_notified', None))


@bp.route('/htmx/renew-allocations/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_renew_allocations(project):
    """Create renewed allocations for the selected resources."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    return _RenewAllocationsHandler(project=project, root=root).handle()


@bp.route('/htmx/renew-allocations-preview/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_renew_allocations_preview(project):
    """The renewal notice the Renew form would send, from its current values."""
    from sam.manage.renew import plan_renew_allocations
    root = project.get_root() if hasattr(project, 'get_root') else project

    def plan(data, source_dt):
        return plan_renew_allocations(
            db.session, root_project_id=root.project_id, source_active_at=source_dt,
            new_start=datetime.combine(data['new_start_date'], datetime.min.time()),
            new_end=data['new_end_date'], resource_ids=data['resource_ids'],
            scales=data.get('scales') or {},
            replace_existing=data.get('replace_existing', False))

    def refusal(data, source_dt):
        errors = _renew_preconditions(
            root, source_dt, datetime.combine(data['new_start_date'], datetime.min.time()),
            data['new_end_date'], data['resource_ids'],
            data.get('replace_existing', False))['errors']
        return ' '.join(errors) or None

    return _renewal_preview(
        project, root, action='renewed', schema_cls=RenewAllocationsForm,
        form_input=_renew_form_input, plan=plan, refusal=refusal,
        active_at=lambda data, _src: datetime.combine(data['new_start_date'],
                                                      datetime.min.time()),
        endpoint='admin_dashboard.htmx_renew_allocations_preview', prefix='renewPreview')


# ---------------------------------------------------------------------------
# Extend allocations (Edit Project -> Allocations tab)
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


@bp.route('/htmx/extend-allocations-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_extend_allocations_form(project):
    """Return the Extend Allocations modal form fragment."""
    root = project.get_root() if hasattr(project, 'get_root') else project

    source_active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates = _build_alloc_candidates(root, source_active_at)

    default_end = _propose_extend_end(
        [a for a in _proposal_sources(candidates) if a.end_date is not None])

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
        return _extend_form_input()

    def clean(self, data):
        self.new_end = data['new_end_date']   # datetime via post_load
        self.notify_leads = data.get('notify_leads', False)
        self.operator_comment = data.get('operator_comment')
        self.source_dt = datetime.combine(
            data['source_active_at'], datetime.min.time())

        error = _extend_shortening_error(self.root, data['resource_ids'],
                                         self.source_dt, self.new_end)
        if error:
            raise FormError(error)
        return data

    def perform(self, data):
        from sam.manage.extend import extend_project_allocations
        self.touched = []
        updated = extend_project_allocations(
            db.session,
            root_project_id=self.root.project_id,
            source_active_at=self.source_dt,
            new_end=self.new_end,
            resource_ids=data['resource_ids'],
            user_id=current_user.user_id,
            touched=self.touched,
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
            'candidates': _build_alloc_candidates(self.root, source_dt),
            'source_active_at': source_dt.strftime('%Y-%m-%d'),
            'default_end': request.form.get('new_end_date', ''),
        }

    def after_commit(self, updated):
        # Extend leaves start dates alone; land the PI on the source date
        # where the now-extended allocations live.
        self._notified = _maybe_notify_renewal(
            self.root, action='extended', new_end=self.new_end,
            active_at=self.source_dt, touched=self.touched,
            notify_leads=self.notify_leads, comment=self.operator_comment)

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, updated):
        return (
            f'{self.root.projcode}: extended {len(updated)} allocation(s) to '
            f'{self.new_end.strftime("%Y-%m-%d")}'
            + _notified_suffix(getattr(self, '_notified', None))
        )


@bp.route('/htmx/extend-allocations/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_extend_allocations(project):
    """Push end_date forward on the selected allocations."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    return _ExtendAllocationsHandler(project=project, root=root).handle()


@bp.route('/htmx/extend-allocations-preview/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_extend_allocations_preview(project):
    """The extension notice the Extend form would send, from its current values."""
    from sam.manage.extend import plan_extend_allocations
    root = project.get_root() if hasattr(project, 'get_root') else project

    def plan(data, source_dt):
        return plan_extend_allocations(
            db.session, root_project_id=root.project_id, source_active_at=source_dt,
            new_end=data['new_end_date'], resource_ids=data['resource_ids'])

    return _renewal_preview(
        project, root, action='extended', schema_cls=ExtendAllocationsForm,
        form_input=_extend_form_input, plan=plan, active_at=lambda _data, src: src,
        endpoint='admin_dashboard.htmx_extend_allocations_preview', prefix='extendPreview',
        refusal=lambda data, src: _extend_shortening_error(
            root, data['resource_ids'], src, data['new_end_date']))


# ---------------------------------------------------------------------------
# Align allocations — one uniform [min(start), max(end)] period of performance
# ---------------------------------------------------------------------------

def _align_context(project, root, source_dt):
    """Grid rows + computed target for the Align modal (GET and error re-render)."""
    from sam.manage.allocations import alignment_conflicts, alignment_target
    from sam.manage.renew import find_source_allocations_at
    sources = find_source_allocations_at(db.session, root, source_dt)
    target = alignment_target(sources)
    candidates = sorted((
        {'resource_name': s.account.resource.resource_name,
         'resource_type': (s.account.resource.resource_type.resource_type
                           if s.account.resource.resource_type else ''),
         'start_date': s.start_date, 'end_date': s.end_date,
         'is_open_ended': s.end_date is None,
         'conflict': bool(target and s.end_date is not None
                          and (s.start_date, s.end_date) != target
                          and alignment_conflicts(s, *target))}
        for s in sources), key=lambda c: c['resource_name'])
    misaligned = target is not None and any(
        not c['is_open_ended'] and (c['start_date'], c['end_date']) != target
        for c in candidates)
    return {
        'project': project, 'root': root,
        'source_active_at': source_dt.strftime('%Y-%m-%d'),
        'candidates': candidates,
        'target_start': target[0] if target else None,
        'target_end': target[1] if target else None,
        'misaligned': misaligned,
        'blocked': any(c['conflict'] for c in candidates),
    }


@bp.route('/htmx/align-allocations-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_align_allocations_form(project):
    """Return the Align Allocations modal form fragment."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    source_dt = _parse_active_at_arg(request.args.get('active_at', ''))
    return render_template(
        'dashboards/admin/fragments/align_allocations_form_htmx.html',
        **_align_context(project, root, source_dt))


class _AlignAllocationsHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Set every dated resource to one uniform period of performance."""

    schema_cls = AlignAllocationsForm
    template = 'dashboards/admin/fragments/align_allocations_form_htmx.html'
    error_prefix = 'Error aligning allocations'
    success_message = 'Allocations aligned successfully.'

    def clean(self, data):
        self.source_dt = datetime.combine(
            data['source_active_at'], datetime.min.time())
        return data

    def perform(self, data):
        from sam.manage.allocations import align_project_allocations
        changed = align_project_allocations(
            db.session, root_project_id=self.root.project_id,
            source_active_at=self.source_dt, user_id=current_user.user_id)
        if not changed:
            raise FormError(
                'Nothing to align — the resources already share a period, or '
                'there are fewer than two dated resources.')
        return changed

    def context(self):
        return _align_context(self.project, self.root, self.source_dt)

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadAllocationTree': self.project.projcode}

    def detail(self, changed):
        start, end = changed[0].start_date, changed[0].end_date
        return (f'{self.root.projcode}: aligned {len(changed)} resource(s) to '
                f'{start.strftime("%Y-%m-%d")} → {end.strftime("%Y-%m-%d")}')


@bp.route('/htmx/align-allocations/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_align_allocations(project):
    """Set every dated resource to one uniform period of performance."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    return _AlignAllocationsHandler(project=project, root=root).handle()


# ---------------------------------------------------------------------------
# Manual "Notify" — tell the lead/admin of each changed project in the tree
# ---------------------------------------------------------------------------

def _public_root():
    from webapp.utils.notify import public_url_root
    return public_url_root()


def _notify_url_builder():
    """projcode -> that project's Edit-page allocations deep link (external)."""
    from webapp.utils.notify import public_url_for
    return lambda pc: public_url_for('admin_dashboard.edit_project_page',
                                     projcode=pc, tab='allocations')


@bp.route('/htmx/notify-project-form/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_notify_project_form(project):
    """The manual Notify modal: the project tree with per-row New/Adjustment/Skip."""
    from sam.queries.lifecycle_notices import classify_tree_for_notice
    from webapp.utils.notify import notify_config
    root = project.get_root() if hasattr(project, 'get_root') else project
    active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    notices = classify_tree_for_notice(db.session, root, active_at=active_at)
    cfg = notify_config()
    return render_template(
        'dashboards/admin/fragments/notify_project_form_htmx.html',
        project=project, root=root, active_at=active_at.strftime('%Y-%m-%d'),
        notices=notices, notify_enabled=cfg.enabled,
        redirect_to=cfg.redirect_to or None)


@bp.route('/htmx/notify-project-preview/<projcode>')
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_notify_project_preview(project):
    """Render one project's lifecycle message for the modal's preview pane."""
    from sam.queries.lifecycle_notices import (
        build_lifecycle_messages, notice_for_project)
    from webapp.utils.email_preview import (
        email_preview_context, render_preview_info)
    # The changed row includes only itself, so the lone action_* param is the
    # selection ('skip' included: the pane then says so); the self-refresh
    # and the recipient picker pass a plain ?action=.
    action = next((v for k, v in request.args.items() if k.startswith('action_')),
                  request.args.get('action', 'activated'))
    if action not in ('activated', 'adjusted'):
        return render_preview_info(
            f'{project.projcode} is set to Skip: it will not be notified.',
            'secondary')
    active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    notice = notice_for_project(db.session, project, active_at=active_at)
    item = notice.item_for(action) if notice else None
    messages = build_lifecycle_messages(
        db.session, per_project=[item],
        requested_by=current_user.username,
        url_builder=_notify_url_builder(),
        operator_comment=request.args.get('operator_comment', '')[:1000],
        site_url=_public_root()) if item is not None else []
    refresh_url = url_for('admin_dashboard.htmx_notify_project_preview',
                          projcode=project.projcode, action=action,
                          active_at=active_at.strftime('%Y-%m-%d'))
    pane = email_preview_context(
        messages, id_prefix='notifyPreview', pane_id='notifyPreviewPane',
        picker_url=refresh_url, picker_method='get',
        picker_include='#notifyOperatorComment', mode_banner=False,
        empty=f'Nothing to preview for {project.projcode} (no recipient on file).')
    return render_template(
        'dashboards/admin/fragments/notify_project_preview_htmx.html',
        refresh_url=refresh_url, **pane)


@bp.route('/htmx/notify-project/<projcode>', methods=['POST'])
@login_required
@require_project_facility_permission(Permission.EDIT_ALLOCATIONS)
def htmx_notify_project(project):
    """Send the manual lifecycle notices per the operator's per-row choices."""
    from sam.queries.lifecycle_notices import (
        build_lifecycle_messages, classify_tree_for_notice)
    from marshmallow import ValidationError
    from webapp.utils.notify import get_notifier, notify_config, notify_summary
    root = project.get_root() if hasattr(project, 'get_root') else project
    active_at = _parse_active_at_arg(request.form.get('active_at', ''))
    notices = classify_tree_for_notice(db.session, root, active_at=active_at)
    try:
        comment = NotifyProjectForm().load(request.form)['operator_comment']
    except ValidationError as e:
        cfg = notify_config()
        return render_template(
            'dashboards/admin/fragments/notify_project_form_htmx.html',
            project=project, root=root, notices=notices,
            active_at=active_at.strftime('%Y-%m-%d'),
            notify_enabled=cfg.enabled, redirect_to=cfg.redirect_to or None,
            errors=NotifyProjectForm.flatten_errors(e.messages),
            operator_comment=request.form.get('operator_comment', ''))

    # Each row's chosen action defaults to its auto_action. A row the operator
    # includes that the classifier had marked 'skip' (dedup-spent) is a
    # deliberate resend -> the force pass.
    auto_items, forced_items = [], []
    for n in notices:
        chosen = request.form.get(f'action_{n.project.project_id}', n.auto_action)
        item = n.item_for(chosen) if chosen in ('activated', 'adjusted') else None
        if item is None:
            continue
        (forced_items if n.auto_action == 'skip' else auto_items).append(item)

    url_builder = _notify_url_builder()

    def _send(items, *, force):
        if not items:
            return []
        msgs = build_lifecycle_messages(
            db.session, per_project=items,
            requested_by=current_user.username, url_builder=url_builder,
            operator_comment=comment, site_url=_public_root())
        return get_notifier().send_many(msgs, force=force) if msgs else []

    summary = notify_summary(_send(auto_items, force=False)
                             + _send(forced_items, force=True))
    return render_template(
        'dashboards/admin/fragments/notify_project_result_htmx.html',
        summary=summary, projcode=root.projcode)


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
                return acct is not None and bool(acct.live_allocations)
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
    from marshmallow import ValidationError
    from sam.manage.allocations import link_allocation_to_parent
    from sam.schemas.forms import LinkAllocationParentForm

    projcode = allocation.account.project.projcode

    try:
        form_data = LinkAllocationParentForm().load(request.form)
    except ValidationError as e:
        msgs = '; '.join(m for ms in e.messages.values() for m in ms)
        return f'<div class="alert alert-danger">{msgs}</div>', 400
    parent_allocation_id = form_data['parent_allocation_id']

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
        return acct is not None and bool(acct.live_allocations)

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
        # Contracts are not filtered — an expired grant stays visible as
        # funding provenance — but current ones sort first and the table
        # badges the lapsed rows.
        contracts=project.contracts_current_first(),
        active_directories=[pd for pd in project.directories if pd.is_active],
        disk_roots=_disk_roots_for_picker(),
        can_edit_governance=can_edit_project_governance(current_user, project),
        # Gated on the contract_card route's own permission so the link
        # can never 403 for a project lead without contract access.
        can_view_contracts=has_permission_any_facility(
            current_user, Permission.VIEW_CONTRACTS),
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
@require_project_permission(Permission.EDIT_PROJECTS, include_ancestors=True)
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
    from sqlalchemy.orm import joinedload
    from sam.projects.projects import ProjectDirectory, Project
    from sam.resources.resources import DiskResourceRootDirectory, Resource

    roots = (
        db.session.query(DiskResourceRootDirectory)
        .options(joinedload(DiskResourceRootDirectory.resource)
                 .joinedload(Resource.resource_type))
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

    # The template reads `pd.project` per row; a Project loaded on its own
    # also fires its selectin `accounts` load, so that is suppressed too.
    q = db.session.query(ProjectDirectory).join(Project).options(
        joinedload(ProjectDirectory.project).lazyload(Project.accounts))
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
        can_view_projects=has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
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
        'can_view_users': has_permission_any_facility(current_user, Permission.VIEW_USERS),
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
    """Give every project member access to every active project resource."""
    from sam.manage import reconcile_project_access

    active_only = _access_grid_active_only(request.form)
    try:
        with management_transaction(db.session):
            reconcile_project_access(db.session, project.project_id)
    except ValueError as e:
        return _render_access_grid(project, active_only, errors=[str(e)])

    return _render_access_grid(project, active_only)
