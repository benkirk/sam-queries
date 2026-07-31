"""The broad net: load every dashboard page, assert the browser stayed quiet.

Catches what the Python tier structurally cannot — dangling ``hx-target``
(htmx emits ``console.error("htmx:targetError")``), uncaught JS exceptions, and
script-order breakage. It does *not* catch a dangling ``data-bs-target``, which
is completely silent in the browser; that is what
``tests/unit/test_modal_shell_contract.py`` is for. The two layers are
complementary by design, not redundant.

Deliberately not an exhaustive crawl. A short list of declared flows keeps the
suite fast and stable; breadth comes from the route sweep.
"""
import pytest

from conftest import (ALLOWED_CONSOLE, dashboard_page_routes,
                      expand_first_project_card, visit)

PAGE_ROUTES = dashboard_page_routes()


def test_route_list_is_not_empty():
    """Guard the derivation itself — a bad filter would silently sweep nothing."""
    assert len(PAGE_ROUTES) >= 20, (
        f'only {len(PAGE_ROUTES)} routes derived from the route map snapshot; '
        f'the filter in conftest.dashboard_page_routes() is probably wrong: '
        f'{PAGE_ROUTES}'
    )


@pytest.mark.parametrize('route', PAGE_ROUTES)
def test_page_loads_without_console_errors(page, errors, route):
    visit(page, route)
    found = errors.drain()
    assert not found, f'{route} produced browser errors:\n' + '\n'.join(
        f'  {entry}' for entry in found)


# ---------------------------------------------------------------------------
# Declared flows — the interactions that broke in PR #378, plus the affordances
# most likely to hide a dangling target (anything that opens a modal).
# ---------------------------------------------------------------------------

def test_admin_project_search_to_edit_pencil(page, errors):
    """/admin/projects search -> first result -> project card -> edit pencil.

    Bug #1's exact path. The pencil lives inside an htmx-loaded card and targets
    a modal shell defined by the page, so it exercises the page/fragment seam.
    """
    visit(page, '/admin/projects')

    # The active_toggle_search macro (dashboards/fragments/search_box.html)
    # names the input via input_id and posts as `q` on a 300ms input delay.
    page.fill('#projectSearchInput', 'SCSG')
    results = page.locator('#projectSearchResults button.list-group-item-action')
    try:
        results.first.wait_for(state='visible', timeout=10_000)
    except Exception:
        pytest.skip('no project search results in this dataset')
    results.first.click()

    page.wait_for_selector('#projectCardContainer', state='attached')
    page.wait_for_load_state('networkidle')

    found = errors.drain()
    assert not found, 'admin project search flow produced browser errors:\n' + '\n'.join(
        f'  {entry}' for entry in found)


@pytest.mark.parametrize('route', [
    '/allocations/transactions',
    '/allocations/adjustments',
])
def test_allocations_tables_open_project_modal(page, errors, route):
    """Open a project-details modal, then the edit pencil inside it.

    These are the pages that carried the dead pencil. The seam under test is
    page/fragment: the modal body arrives by htmx and reaches for shells the
    *page* has to provide, so a missing shell fires htmx:targetError right
    here. Openers are anchors carrying `hx-get=.../project-details-modal/...`,
    not a `data-bs-target` — the modal is shown by the swap.
    """
    visit(page, route)

    opener = page.locator('[hx-get*="project-details-modal"]').first
    try:
        opener.wait_for(state='visible', timeout=10_000)
    except Exception:
        pytest.skip(f'no project-details opener rendered on {route}')
    opener.click()

    page.wait_for_selector('#projectDetailsModal.show', timeout=10_000)
    page.wait_for_load_state('networkidle')

    pencil = page.locator('#projectDetailsModalBody [data-bs-target="#editAllocationModal"]')
    if pencil.count():
        pencil.first.click()
        page.wait_for_timeout(800)      # let the htmx GET + modal animation land

    found = errors.drain()
    assert not found, f'{route} modal flow produced browser errors:\n' + '\n'.join(
        f'  {entry}' for entry in found)


def test_user_accounts_edit_pencil(page, errors):
    """/user/accounts renders the project cards — and their pencils — inline.

    The one page where the bug-#1 affordance is reachable without first driving
    a chart or a table filter, so it is the cheapest full exercise of
    pencil -> Bootstrap modal -> htmx form swap.
    """
    visit(page, '/user/accounts')

    pencil = expand_first_project_card(page)
    pencil.click()
    page.wait_for_selector('#editAllocationModal.show', timeout=10_000)
    page.wait_for_load_state('networkidle')

    found = errors.drain()
    assert not found, '/user/accounts pencil flow produced browser errors:\n' + '\n'.join(
        f'  {entry}' for entry in found)


def test_admin_resources_card_modals(page, errors):
    """Admin cards build their shells with the modal_scaffold macro rather than
    literal markup — a different code path to the shared includes, worth one
    flow of its own."""
    visit(page, '/admin/resources')

    opener = page.locator('[data-bs-target="#createResourceModal"]').first
    if opener.count() == 0:
        pytest.skip('create-resource affordance not available to this user')
    opener.click()
    page.wait_for_selector('#createResourceModal.show', timeout=10_000)
    page.wait_for_load_state('networkidle')

    found = errors.drain()
    assert not found, '/admin/resources modal flow produced browser errors:\n' + '\n'.join(
        f'  {entry}' for entry in found)


def test_console_allowlist_has_no_dead_entries(page, errors):
    """Ratchet: every ALLOWED_CONSOLE pattern must still be earning its place.

    Without this the allowlist only ever grows, and a pattern added for a bug
    that was later fixed keeps silently suppressing its own regression. The
    allowlist is empty today, which is the state to defend.
    """
    if not ALLOWED_CONSOLE:
        return

    visit(page, '/user/accounts')
    visit(page, '/admin/projects')

    matched = {
        pattern.pattern for pattern in ALLOWED_CONSOLE
        for text in errors.console if pattern.search(text)
    }
    dead = sorted({p.pattern for p in ALLOWED_CONSOLE} - matched)
    assert not dead, (
        f'ALLOWED_CONSOLE patterns matched nothing on this run: {dead}. '
        'Either the noise they excused is fixed — delete them from '
        'e2e/conftest.py — or they never matched in the first place.'
    )
