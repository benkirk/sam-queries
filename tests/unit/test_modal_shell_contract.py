"""Front-end shell contracts: every modal/htmx target must resolve.

Replaces ``test_modal_shell_pairing.py`` (PR #378), which pinned one specific
pairing. This generalizes it to the whole template tree.

**Why this tier exists.** PR #378 fixed a dead edit pencil: the project-details
modal body rendered per-allocation pencils whose ``data-bs-target``/``hx-target``
pointed at ``#editAllocationModal``/``#editAllocationFormContainer``, but the
fragment defining those ids was not included on ``/status/*`` or
``/allocations/{transactions,adjustments}``. The failure was **completely
silent**: Bootstrap found no modal, htmx aborted on a dangling target, and
nothing reached the console or the network tab. A browser-driven sweep catches
the htmx half (htmx does ``console.error("htmx:targetError")``), but a dangling
``data-bs-target`` produces no output at all — so this Python tier is the only
thing that can see it. See ``docs/plans/implemented/FRONTEND_TEST_NET.md``.

Three checks, in increasing cost:

1. :func:`test_page_template_targets_resolve` — static. Every ``#id`` targeted
   from within a page template's ``{% extends %}``/``{% include %}`` closure is
   defined somewhere in that same closure. No DB, no Flask, no rendering.
2. :func:`test_htmx_fragment_shell_deps_match_pin` — the ratchet. Fragments
   loaded *at runtime* by htmx are not in any page's static closure, so check 1
   cannot see them. Their cross-boundary dependencies are pinned here so a new
   one has to be looked at rather than silently trusted.
3. :func:`test_project_details_fragment_targets_resolve` — rendered. Walks the
   exact chain PR #378 broke, through the real Flask test client.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'

_EXTENDS = re.compile(r"{%-?\s*extends\s+['\"]([^'\"]+)['\"]")
_INCLUDE = re.compile(r"{%-?\s*include\s+['\"]([^'\"]+)['\"]")
_ID = re.compile(r'\bid="([A-Za-z0-9_-]+)"')
_TARGET = re.compile(r'\b(?:data-bs-target|hx-target)="#([A-Za-z0-9_-]+)"')
# Most shells are built by the modal_scaffold() macro rather than literal
# markup, so their ids never appear as id="..." in the calling template:
#   {{ modal_scaffold('createProjectModal', 'New Project',
#                     icon='folder-plus', container_id='createProjectFormContainer') }}
_SCAFFOLD = re.compile(r"modal_scaffold\(\s*'([A-Za-z0-9_]+)'(.*?)\)", re.S)
_CONTAINER_KWARG = re.compile(r"container_id\s*=\s*'([A-Za-z0-9_]+)'")


def _load_templates():
    return {
        str(path.relative_to(TEMPLATE_ROOT)): path.read_text()
        for path in TEMPLATE_ROOT.rglob('*.html')
    }


TEMPLATES = _load_templates()


def _ids_defined_by(template):
    """Ids this one template puts in the DOM — literal and macro-generated."""
    ids = set(_ID.findall(TEMPLATES[template]))
    for modal_id, rest in _SCAFFOLD.findall(TEMPLATES[template]):
        ids.add(modal_id)
        ids.update(_CONTAINER_KWARG.findall(rest))
    return ids


def _closure(template, seen=None):
    """`template` plus everything it extends/includes, transitively.

    Safe to walk statically: the tree has no dynamic ``{% include some_var %}``.
    """
    seen = set() if seen is None else seen
    if template in seen or template not in TEMPLATES:
        return seen
    seen.add(template)
    for child in set(_EXTENDS.findall(TEMPLATES[template])) | set(_INCLUDE.findall(TEMPLATES[template])):
        _closure(child, seen)
    return seen


def _ids_in_closure(template):
    return set().union(*(_ids_defined_by(t) for t in _closure(template)))


def _targets_in_closure(template):
    return {
        (t, ref)
        for t in _closure(template)
        for ref in _TARGET.findall(TEMPLATES[t])
    }


PAGE_TEMPLATES = sorted(t for t, src in TEMPLATES.items() if _EXTENDS.search(src))


# Every modal shell in the tree, however it is built. A fragment referencing one
# of these (or its body/form container) is reaching *outside itself* for markup
# the host page has to supply — which is the failure mode this module guards.
def _all_modal_shells():
    shells = set(re.compile(r'class="modal fade"[^>]*id="([A-Za-z0-9_-]+)"').findall(
        '\n'.join(TEMPLATES.values())))
    for src in TEMPLATES.values():
        for modal_id, rest in _SCAFFOLD.findall(src):
            shells.add(modal_id)
            shells.update(_CONTAINER_KWARG.findall(rest))
    return shells


MODAL_SHELLS = _all_modal_shells()
SHELL_MARKUP = MODAL_SHELLS | {f'{s}Body' for s in MODAL_SHELLS} | {f'{s}FormContainer' for s in MODAL_SHELLS}


def _htmx_only_fragment_deps():
    """{fragment template: [shell ids its host page must provide]}.

    Templates unreachable from any page closure are loaded by htmx at runtime.
    Their unmet shell references are exactly the cross-boundary contract that
    static analysis cannot verify.
    """
    statically_reachable = set().union(*(_closure(p) for p in PAGE_TEMPLATES))
    deps = {}
    for template in sorted(TEMPLATES):
        if template in statically_reachable:
            continue
        own = _ids_in_closure(template)
        missing = sorted((set(_TARGET.findall(TEMPLATES[template])) - own) & SHELL_MARKUP)
        if missing:
            deps[template] = missing
    return deps


# ---------------------------------------------------------------------------
# 1. Static: page closures are self-consistent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('page', PAGE_TEMPLATES)
def test_page_template_targets_resolve(page):
    """Every ``#id`` a page targets is defined somewhere in the same page.

    This is the check that would have caught PR #378 at author time: dropping
    the ``allocation_modals.html`` include from ``project_details_modal.html``
    leaves ``#editAllocationModal`` referenced and undefined, and this fails
    naming both the page and the template that made the reference.
    """
    defined = _ids_in_closure(page)
    dangling = sorted(
        f'#{ref}  (referenced by {source})'
        for source, ref in _targets_in_closure(page)
        if ref not in defined
    )
    assert not dangling, (
        f'{page} targets ids that nothing in its template closure defines.\n'
        + '\n'.join(f'  {d}' for d in dangling)
        + '\n\nEither the shell fragment is missing an {% include %}, or the '
          'target is loaded by htmx at runtime — in which case add the '
          'fragment to HTMX_FRAGMENT_SHELL_DEPS below instead.'
    )


# ---------------------------------------------------------------------------
# 2. Ratchet: runtime-loaded fragments and what they expect from their host
# ---------------------------------------------------------------------------

# Fragment template -> modal shells its *host page* must already carry.
# Derived mechanically (see _htmx_only_fragment_deps) and pinned by equality so
# that a new cross-boundary dependency has to be looked at. When this fires:
# confirm every page that can load the fragment ships those shells, then update
# the pin. Same ratchet shape as tests/unit/test_template_csp_lint.py.
HTMX_FRAGMENT_SHELL_DEPS = {
    'dashboards/admin/fragments/bulk_deactivate_project_directories_form_htmx.html': [
        'bulkDeactivateProjectDirectoriesFormContainer'],
    'dashboards/admin/fragments/bulk_deactivate_project_directories_preview_htmx.html': [
        'bulkDeactivateProjectDirectoriesFormContainer'],
    'dashboards/admin/fragments/contract_award_candidates_htmx.html': [
        'createContractFormContainer', 'createContractModal'],
    'dashboards/admin/fragments/contract_card.html': [
        'projectDetailsModalBody'],
    'dashboards/admin/fragments/contracts_table_htmx.html': [
        'contractDetailsModalBody', 'createContractFormContainer', 'createContractModal',
        'createContractSourceFormContainer', 'createContractSourceModal'],
    'dashboards/admin/fragments/disk_root_directories_section.html': [
        'addDiskRootFormContainer', 'addDiskRootModal'],
    'dashboards/admin/fragments/facility_card.html': [
        'createAllocationTypeFormContainer', 'createAllocationTypeModal',
        'createFacilityFormContainer', 'createFacilityModal',
        'createPanelFormContainer', 'createPanelModal'],
    'dashboards/admin/fragments/institutions_table.html': [
        'createInstitutionFormContainer', 'createInstitutionModal',
        'createInstitutionTypeFormContainer', 'createInstitutionTypeModal',
        'createMnemonicCodeFormContainer', 'createMnemonicCodeModal',
        'projectDetailsModal', 'projectDetailsModalBody',
        'userDetailsModal', 'userDetailsModalBody'],
    'dashboards/admin/fragments/nsf_program_contracts_htmx.html': [
        'contractDetailsModalBody'],
    # The delivery log's per-row detail button opens the shared audit modal.
    # Loaded only into dashboards/admin/notifications.html, which includes
    # partials/audit_details_modal.html itself — same arrangement as the XRAS
    # action log, which is the page this one is modelled on.
    'dashboards/admin/fragments/notifications_log.html': [
        'auditDetailsModal', 'auditDetailsModalBody'],
    'dashboards/admin/fragments/organization_card.html': [
        'createAoiFormContainer', 'createAoiGroupFormContainer', 'createAoiGroupModal',
        'createAoiModal', 'createNsfProgramFormContainer', 'createNsfProgramModal',
        'createOrganizationFormContainer', 'createOrganizationModal'],
    'dashboards/admin/fragments/project_allocation_tree_htmx.html': [
        'editAllocationModal'],
    # Only ever loaded by dashboards/admin/scheduled_tasks.html, which includes
    # partials/audit_details_modal.html itself — same arrangement as the
    # notification delivery log above, the page this one is modelled on.
    'dashboards/admin/fragments/scheduled_tasks_log.html': [
        'auditDetailsModal', 'auditDetailsModalBody'],
    'dashboards/admin/fragments/project_directories_card.html': [
        'addProjectDirectoryFormContainer', 'addProjectDirectoryModal',
        'bulkDeactivateProjectDirectoriesFormContainer', 'bulkDeactivateProjectDirectoriesModal',
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/admin/fragments/project_linked_elements_htmx.html': [
        'contractDetailsModalBody', 'editProjectDirectoryFormContainer',
        'editProjectDirectoryModal'],
    'dashboards/admin/fragments/queue_cleanup_preview_htmx.html': [
        'queueCleanupFormContainer'],
    'dashboards/admin/fragments/resources_card.html': [
        'addExemptionFormContainer', 'addExemptionModal',
        'createMachineFormContainer', 'createMachineModal',
        'createQueueFormContainer', 'createQueueModal',
        'createResourceFormContainer', 'createResourceModal',
        'createResourceTypeFormContainer', 'createResourceTypeModal',
        'queueCleanupFormContainer', 'queueCleanupModal'],
    'dashboards/allocations/partials/adjustments_table.html': [
        'auditDetailsModal', 'auditDetailsModalBody',
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/allocations/partials/project_table.html': [
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/allocations/partials/transactions_table.html': [
        'auditDetailsModal', 'auditDetailsModalBody',
        'projectDetailsModal', 'projectDetailsModalBody'],
    # The XRAS trio loads only into dashboards/allocations/xras.html, which
    # includes partials/audit_details_modal.html itself and inherits
    # #projectDetailsModal from base_allocations.html — same arrangement as the
    # transactions and adjustments tables above.
    'dashboards/allocations/partials/xras_action_details_modal.html': [
        'auditDetailsModalBody'],
    # The notify preview: its Send button re-targets the same modal body it was
    # itself swapped into, so the outcome (sent / manual fallback) replaces the
    # preview in place. Reached only from the pending card, i.e. the same single
    # host page as the dismiss form directly below.
    'dashboards/allocations/partials/xras_notify_form.html': [
        'auditDetailsModalBody'],
    # The pending card's action buttons open the shared audit modal for the
    # notify preview, the dismiss form and the activation history. Same shell,
    # same single host page (xras.html includes partials/audit_details_modal.html).
    'dashboards/allocations/partials/xras_activity_card.html': [
        'auditDetailsModal', 'auditDetailsModalBody',
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/allocations/partials/xras_pending_event_form.html': [
        'auditDetailsModalBody'],
    # The Remediations family. Every one of these is reached only from
    # `xras.html`, the same single host page as the pending card above, and it
    # includes partials/audit_details_modal.html — verified by grepping the
    # routes in `xras_remediation_routes.py`, all of which render into that
    # page's fragments and nowhere else.
    #
    # `xras_accounts_card.html` joins the list because the Accounts Needed tab
    # now offers the same merge modal on a stuck placeholder — deliberately the
    # same shell, so an operator meets one merge dialogue however they arrive.
    #
    # The three cards below also reach #userDetailsModal / #projectDetailsModal:
    # a username or projcode the row has ALREADY resolved against SAM opens the
    # shared entity modal, same as everywhere else in the app. Both shells come
    # from base_allocations.html (:31 project, :33 user), which xras.html
    # extends — so the single host page carries them.
    'dashboards/allocations/partials/xras_accounts_card.html': [
        'auditDetailsModal', 'auditDetailsModalBody',
        'userDetailsModal', 'userDetailsModalBody'],
    'dashboards/allocations/partials/xras_pending_requests_card.html': [
        'userDetailsModal', 'userDetailsModalBody'],
    'dashboards/allocations/partials/xras_remediations_card.html': [
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/allocations/partials/xras_remediation_row.html': [
        'auditDetailsModal', 'auditDetailsModalBody'],
    'dashboards/allocations/partials/xras_merge_form.html': [
        'auditDetailsModalBody'],
    'dashboards/allocations/partials/xras_action_form.html': [
        'auditDetailsModalBody'],
    'dashboards/allocations/partials/xras_roles_form.html': [
        'auditDetailsModalBody'],
    'dashboards/allocations/partials/xras_pending_history_modal.html': [
        'auditDetailsModalBody'],
    'dashboards/allocations/partials/xras_table.html': [
        'auditDetailsModal', 'auditDetailsModalBody',
        'projectDetailsModal', 'projectDetailsModalBody'],
    'dashboards/fragments/contract_bits.html': [
        'nsfProgramContractsModalBody', 'userDetailsModalBody'],
    'dashboards/fragments/user_rows.html': [
        'userDetailsModalBody'],
    'dashboards/shared/project_tree.html': [
        'allocateDownModal', 'editAllocationModal', 'exchangeAllocationModal'],
    'dashboards/user/partials/jobs_histogram.html': [
        'projectDetailsModal', 'projectDetailsModalBody',
        'userDetailsModal', 'userDetailsModalBody'],
    'dashboards/user/partials/project_card.html': [
        'contractDetailsModalBody', 'editAllocationModal'],
    'dashboards/user/partials/user_card.html': [
        'addExemptionFormContainer', 'addExemptionModal',
        'editExemptionFormContainer', 'editExemptionModal', 'groupMembersModal',
        'projectDetailsModal', 'projectDetailsModalBody'],
    'project_members/fragments/members_table.html': [
        'addMemberModal', 'userDetailsModal', 'userDetailsModalBody'],
}


def test_htmx_fragment_shell_deps_match_pin():
    """Pin which runtime-loaded fragments depend on host-page modal shells."""
    actual = _htmx_only_fragment_deps()

    added = {k: v for k, v in actual.items() if k not in HTMX_FRAGMENT_SHELL_DEPS}
    removed = sorted(set(HTMX_FRAGMENT_SHELL_DEPS) - set(actual))
    changed = {
        k: (HTMX_FRAGMENT_SHELL_DEPS[k], v)
        for k, v in actual.items()
        if k in HTMX_FRAGMENT_SHELL_DEPS and v != HTMX_FRAGMENT_SHELL_DEPS[k]
    }

    msg = []
    if added:
        msg.append(
            'New htmx fragment(s) reaching for host-page modal shells. Confirm '
            'EVERY page that can load them ships those shells (grep the route '
            'that renders the fragment), then add to HTMX_FRAGMENT_SHELL_DEPS:')
        msg += [f'  {k}: {v}' for k, v in sorted(added.items())]
    if removed:
        msg.append('Fragment(s) no longer depend on host shells — drop from the pin:')
        msg += [f'  {k}' for k in removed]
    if changed:
        msg.append('Dependency set changed:')
        msg += [f'  {k}\n      pinned: {old}\n      actual: {new}'
                for k, (old, new) in sorted(changed.items())]

    assert not msg, '\n'.join(msg)


# ---------------------------------------------------------------------------
# 3. Rendered: the exact chain PR #378 broke
# ---------------------------------------------------------------------------

PROJECT_MODAL_ID = 'id="projectDetailsModal"'
EDIT_MODAL_ID = 'id="editAllocationModal"'
EDIT_CONTAINER_ID = 'id="editAllocationFormContainer"'

# Pages that render the project-details modal shell and are reachable as a URL.
# `test_project_modal_page_list_is_complete` keeps this honest: a new page that
# ships the shell forces an entry here rather than escaping coverage.
PAGES_WITH_PROJECT_MODAL = {
    '/user/accounts': 'dashboards/user/accounts.html',
    '/user/info': 'dashboards/user/info.html',
    '/user/jobs': 'dashboards/user/my_jobs.html',
    '/user/data': 'dashboards/user/my_data.html',
    '/admin/projects': 'dashboards/admin/projects.html',
    '/admin/projects/directories': 'dashboards/admin/projects_directories.html',
    '/admin/organizations': 'dashboards/admin/organizations.html',
    '/admin/resources': 'dashboards/admin/resources.html',
    '/admin/facilities': 'dashboards/admin/facilities.html',
    '/admin/contracts': 'dashboards/admin/contracts.html',
    '/admin/configuration': 'dashboards/admin/configuration.html',
    '/admin/users-groups': 'dashboards/admin/users_groups.html',
    '/allocations/projects': 'dashboards/allocations/projects.html',
    '/allocations/transactions': 'dashboards/allocations/transactions.html',
    '/allocations/adjustments': 'dashboards/allocations/adjustments.html',
    '/allocations/xras': 'dashboards/allocations/xras.html',
    '/status/derecho': 'dashboards/status/derecho_page.html',
    '/status/casper': 'dashboards/status/casper_page.html',
    '/status/jupyterhub': 'dashboards/status/jupyterhub_page.html',
    '/status/events': 'dashboards/status/events_page.html',
    '/status/filesystem-scans': 'dashboards/status/filesystem_scans_page.html',
    '/status/job-history': 'dashboards/status/job_history_page.html',
}


def test_project_modal_page_list_is_complete():
    """Every *page* template shipping the shell is represented above.

    Base templates and htmx-loaded sub-pages are excluded — they have no URL of
    their own. The point is that adding a new top-level page which ships the
    project-details modal cannot silently skip the pairing checks below.
    """
    shipped = {
        page for page in PAGE_TEMPLATES
        if 'projectDetailsModal' in _ids_in_closure(page)
        and not Path(page).name.startswith('base_')
    }
    # Pages reached only from another page (not a top-level route) — they
    # inherit their shells from the same bases and are covered transitively.
    NOT_TOP_LEVEL = {
        'dashboards/admin/edit_project.html',       # /admin/project/<projcode>/edit
        'dashboards/user/resource_details.html',    # /user/resource/<name>
        'dashboards/user/jobs_explore_page.html',   # /user/jobs/explore
        'dashboards/status/queue_history.html',     # /status/<machine>/queues
    }
    missing = sorted(shipped - NOT_TOP_LEVEL - set(PAGES_WITH_PROJECT_MODAL.values()))
    assert not missing, (
        'These page templates ship #projectDetailsModal but are not covered by '
        'PAGES_WITH_PROJECT_MODAL. Add each with its URL (or to NOT_TOP_LEVEL '
        'if it has no top-level route):\n'
        + '\n'.join(f'  {m}' for m in missing)
    )


# Routes that `abort(404)` when their optional plugin isn't warm — see
# `my_data()` / `my_jobs()` in webapp/dashboards/user/blueprint.py. The test
# environment loads no plugins, so a 404 here is the designed response, not a
# regression. A 404 on any *other* route still fails.
PLUGIN_GATED = {'/user/data', '/user/jobs'}


@pytest.mark.parametrize('url', sorted(PAGES_WITH_PROJECT_MODAL))
def test_project_modal_pages_ship_the_allocation_modal(auth_client, url):
    """The pencil inside the project-details modal needs its target shell."""
    resp = auth_client.get(url)
    if resp.status_code == 404 and url in PLUGIN_GATED:
        pytest.skip(f'{url} is plugin-gated and the plugin is not loaded here')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count(PROJECT_MODAL_ID) == 1, f'{url}: project modal shell'
    assert html.count(EDIT_MODAL_ID) == 1, f'{url}: edit-allocation shell'
    assert html.count(EDIT_CONTAINER_ID) == 1, f'{url}: htmx target container'


def test_edit_project_page_ships_one_of_each(auth_client, active_project):
    """/admin/project/<projcode>/edit assembles its own modal set (it extends
    dashboards/base, not base_admin) — it used to carry an inline copy of the
    edit-allocation modal alongside the shared include."""
    resp = auth_client.get(f'/admin/project/{active_project.projcode}/edit')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count(PROJECT_MODAL_ID) == 1
    assert html.count(EDIT_MODAL_ID) == 1
    assert html.count(EDIT_CONTAINER_ID) == 1


def test_project_details_fragment_targets_resolve(auth_client, active_project):
    """The rendered modal body's targets all exist on a page that hosts it.

    The static check above works on template source; this walks the real
    runtime chain — GET the page, GET the htmx fragment that fills the modal
    body, and confirm every shell the fragment reaches for is actually there.
    This is precisely what silently failed before PR #378.
    """
    page = auth_client.get('/status/derecho')
    assert page.status_code == 200
    page_ids = set(_ID.findall(page.get_data(as_text=True)))

    fragment = auth_client.get(f'/user/project-details-modal/{active_project.projcode}')
    assert fragment.status_code == 200
    fragment_html = fragment.get_data(as_text=True)

    available = page_ids | set(_ID.findall(fragment_html))
    dangling = sorted(
        ref for ref in set(_TARGET.findall(fragment_html))
        if ref in SHELL_MARKUP and ref not in available
    )
    assert not dangling, (
        'The project-details modal body targets shells that /status/derecho '
        f'does not ship: {dangling}. The shell fragment defining them needs to '
        'be included from dashboards/shared/project_details_modal.html.'
    )


# ---------------------------------------------------------------------------
# 4. Permission-gate parity: a visible opener must have a visible shell
# ---------------------------------------------------------------------------
#
# Checks 1-3 resolve ids *textually* — they see the ``modal_scaffold(...)``
# call and are satisfied, never evaluating the ``{% if has_permission(...) %}``
# wrapped around it. So a shell can be gated on one permission while the button
# opening it is gated on another, and the id resolves statically while the DOM
# is empty at runtime for anyone holding only the opener's permission.
#
# That is not hypothetical: ``organization_modals.html`` gated every create
# shell on CREATE_RESOURCES (copied from ``resources_modals.html``) while the
# contract/org create buttons and their routes gate on CREATE_ORG_METADATA.
# When PR #410 granted nusd/csg CREATE_ORG_METADATA, those groups got visible
# "Create contract" / "Create Source" / "New Organization" buttons whose modal
# and htmx target were both absent — the click no-opped with an
# htmx:targetError and no request at all.
#
# Only one direction is dangerous: an opener visible to someone the shell is
# hidden from. A shell that is *ungated* can never go missing, so those pairs
# are skipped rather than pinned.

_IF_TAG = re.compile(r'{%-?\s*(if|elif|else|endif)\b([^%]*)%}')
_HAS_PERM = re.compile(r'has_permission(?:_any_facility)?\(\s*Permission\.([A-Z_]+)\s*\)')

# Fragments that are themselves served only behind the shell's own permission,
# so an ungated opener inside them is unreachable without it. Value is the
# permission the serving route requires.
GATE_PARITY_ROUTE_GATED = {
    'dashboards/admin/fragments/bulk_deactivate_project_directories_form_htmx.html': 'DELETE_PROJECTS',
    'dashboards/admin/fragments/bulk_deactivate_project_directories_preview_htmx.html': 'DELETE_PROJECTS',
    'dashboards/admin/fragments/queue_cleanup_preview_htmx.html': 'DELETE_RESOURCES',
}


def _guard_at(src, pos):
    """Permission guarding offset `pos`, tracking ``{% if %}`` nesting.

    Returns the innermost ``has_permission(Permission.X)`` in force, or None
    when nothing at that point is permission-gated.
    """
    stack = []
    for m in _IF_TAG.finditer(src):
        if m.start() >= pos:
            break
        kind, cond = m.group(1), m.group(2)
        if kind == 'if':
            found = _HAS_PERM.search(cond)
            stack.append(found.group(1) if found else None)
        elif kind == 'elif' and stack:
            found = _HAS_PERM.search(cond)
            stack[-1] = found.group(1) if found else None
        elif kind == 'else' and stack:
            stack[-1] = None
        elif kind == 'endif' and stack:
            stack.pop()
    live = [s for s in stack if s]
    return live[-1] if live else None


def _shell_guards():
    """{shell id: (defining template, guarding permission or None)}."""
    guards = {}
    for template, src in TEMPLATES.items():
        for m in _SCAFFOLD.finditer(src):
            guard = _guard_at(src, m.start())
            for shell_id in [m.group(1)] + _CONTAINER_KWARG.findall(m.group(2)):
                guards[shell_id] = (template, guard)
    return guards


def test_modal_openers_are_gated_like_their_shells():
    """A button's permission gate must match the gate on the shell it opens."""
    shell_guards = _shell_guards()
    mismatches = []
    for template, src in TEMPLATES.items():
        for m in _TARGET.finditer(src):
            ref = m.group(1)
            if ref not in shell_guards:
                continue
            shell_template, shell_guard = shell_guards[ref]
            if shell_guard is None:
                continue          # always in the DOM — cannot go missing
            opener_guard = _guard_at(src, m.start())
            if opener_guard == shell_guard:
                continue
            if GATE_PARITY_ROUTE_GATED.get(template) == shell_guard:
                continue          # route already enforces the same permission
            mismatches.append(
                f'  #{ref}\n'
                f'      shell  gated on {shell_guard}  ({shell_template})\n'
                f'      opener gated on {opener_guard or "nothing"}  ({template})'
            )

    assert not mismatches, (
        'Modal openers are visible to users the shell is hidden from — the '
        'click will silently no-op with an htmx:targetError:\n\n'
        + '\n'.join(sorted(mismatches))
        + '\n\nGate the shell on the same permission its openers (and the '
          'routes behind them) use. If the opener fragment is only ever served '
          'behind that permission, add it to GATE_PARITY_ROUTE_GATED instead.'
    )


# ---------------------------------------------------------------------------
# Script order in dashboards/base.html
# ---------------------------------------------------------------------------

# actions.js defines window.registerAction / window.revealCard; these scripts
# call them at eval time, so they must load after it. Undeclared and
# load-bearing — reordering silently breaks every data-action on every page.
ACTIONS_DEPENDENTS = [
    'pickers.js',
    'dashboard-init.js',
    'admin-cards.js',
    'modals.js',
    'form-helpers.js',
]


def test_actions_js_loads_before_its_dependents():
    base = TEMPLATES['dashboards/base.html']
    order = [m for m in re.findall(r"js/([A-Za-z0-9_.-]+\.js)", base)]
    assert 'actions.js' in order, 'dashboards/base.html no longer loads actions.js'

    actions_at = order.index('actions.js')
    late = [
        script for script in ACTIONS_DEPENDENTS
        if script in order and order.index(script) < actions_at
    ]
    assert not late, (
        f'{late} load before actions.js in dashboards/base.html. They call '
        'window.registerAction / window.revealCard at eval time, so every '
        'data-action on every page breaks silently. Move actions.js first.'
    )
