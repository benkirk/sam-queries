"""Explicit guards for the two bugs that prompted this whole tier (PR #378).

The console sweep would catch bug #1 indirectly, via ``htmx:targetError``.
These assert the *user-visible* outcome instead, so they still fail if htmx
ever stops logging. Bug #2 is only expressible here: it is a geometry
assertion, and jsdom zeroes ``getBoundingClientRect()``, so no JS unit harness
could make it.
"""
import pytest

from conftest import expand_first_project_card, visit


# The pages PR #378 fixed: they render the project-details modal but used to
# omit the allocation-edit shell its body reaches for. Asserting shell presence
# needs no interaction, which is what makes it a reliable guard — the openers on
# these pages sit behind charts or table filters.
PAGES_THAT_REGRESSED = [
    '/status/derecho',
    '/allocations/transactions',
    '/allocations/adjustments',
]


@pytest.mark.parametrize('route', PAGES_THAT_REGRESSED)
def test_pages_ship_the_allocation_edit_shell(page, route):
    """Bug #1, structural half — the shells must be on the page.

    The project-details modal body renders per-allocation pencils targeting
    #editAllocationModal / #editAllocationFormContainer. These pages did not
    ship them, so Bootstrap found no modal, htmx aborted on the dangling
    target, and *nothing* surfaced: no modal, no request, no error.
    """
    visit(page, route)
    assert page.locator('#editAllocationModal').count() == 1, (
        f'{route} does not ship #editAllocationModal — the pencil inside the '
        'project-details modal is a silent no-op again.')
    assert page.locator('#editAllocationFormContainer').count() == 1, (
        f'{route} does not ship #editAllocationFormContainer — htmx has '
        'nowhere to swap the edit form into.')


def test_edit_pencil_opens_a_usable_form(page):
    """Bug #1, functional half — click a pencil, get a real form.

    Driven from /user/accounts, which renders the project cards (and therefore
    the pencils) inline rather than behind a chart click. Proves the whole
    chain: Bootstrap resolves the modal, htmx resolves its target, and the
    swapped-in form is actually usable.
    """
    visit(page, '/user/accounts')

    pencil = expand_first_project_card(page)
    pencil.click()

    page.wait_for_selector('#editAllocationModal.show', timeout=10_000)
    form = page.locator('#editAllocationFormContainer form')
    form.wait_for(state='visible', timeout=10_000)
    save = page.locator('#editAllocationFormContainer button[type="submit"]').first
    assert save.is_visible(), (
        'the edit-allocation modal opened but carries no visible Save button — '
        'the htmx swap did not land.')


def test_search_result_click_lands_on_the_card_title(page):
    """Bug #2 — the scroll overshoot.

    Clicking a search result scrolled past the loaded card's title, because the
    scroll offset was computed before the results list above it was cleared.
    The assertion is the card header's viewport position: on screen and near
    the top, not scrolled off above it.
    """
    visit(page, '/admin/projects')

    page.fill('#projectSearchInput', 'SCSG')
    results = page.locator('#projectSearchResults button.list-group-item-action')
    try:
        results.first.wait_for(state='visible', timeout=10_000)
    except Exception:
        pytest.skip('no project search results in this dataset')
    results.first.click()

    page.wait_for_selector('#projectCardContainer .card', timeout=10_000)
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(800)          # smooth-scroll settle

    header = page.locator('#projectCardContainer .card-header, '
                          '#projectCardContainer .card-title').first
    box = header.bounding_box()
    assert box is not None, 'the loaded project card has no visible header'

    # Above 0 means the title has been scrolled off the top of the viewport —
    # the original bug. A generous lower bound tolerates the sticky navbar.
    assert -10 <= box['y'] <= 260, (
        f"the loaded card's title landed at y={box['y']:.0f}px. Below ~0 means "
        'the scroll overshot it (the PR #378 bug); a large value means it never '
        'scrolled into view.')
