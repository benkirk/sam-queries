"""The expanded sunburst comes back: closing a project quick-view opened from it shows
it again, and Back from a page the quick-view led to reopens it (``?expand=``).

Uses the Allocations page, which needs no plugin; Job History shares the shell and code.
"""
import pytest

from conftest import visit

ROUTE = '/allocations/projects'
_PROJECT_WEDGE = """() => { const a = [...document.querySelectorAll('#chartExpandModalBody svg a')]
    .find(a => (a.getAttribute('xlink:href') || a.getAttribute('href') || '').includes('/user/project-details-modal/'));
    if (!a) return false;
    a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    return true; }"""


def _expand_param(page):
    return page.evaluate("new URL(location.href).searchParams.get('expand')")


def _open_expanded(page):
    visit(page, ROUTE)
    opener = page.locator('[data-chart-expand-opener]:visible').first
    if opener.count() == 0:
        pytest.skip('no expandable chart in this dataset')
    opener.click()
    page.locator('#chartExpandModal.show').wait_for(timeout=10_000)
    page.locator('#chartExpandModalBody svg a').first.wait_for(state='attached', timeout=30_000)
    page.wait_for_timeout(600)   # Bootstrap ignores hide() during the open transition


def _to_quick_view(page):
    if not page.evaluate(_PROJECT_WEDGE):
        pytest.skip('the expanded chart names no project')
    page.locator('#projectDetailsModal.show').wait_for(timeout=10_000)
    page.wait_for_timeout(600)


def test_closing_the_quick_view_returns_to_the_chart(page):
    _open_expanded(page)
    key = _expand_param(page)
    assert key, 'opening the chart records it in the address bar'
    _to_quick_view(page)
    assert _expand_param(page) == key, 'the marker survives the handoff'
    page.locator('#projectDetailsModal .btn-close').click()
    page.locator('#chartExpandModal.show').wait_for(timeout=10_000)
    assert page.locator('#chartExpandModalBody svg').count() == 1, 'shown again, not refetched'
    page.locator('#chartExpandModal .btn-close').click()
    # The final close clears the marker (on hidden.bs.modal, after the fade). The page's
    # CSP forbids wait_for_function's string eval, so poll.
    for _ in range(50):
        if _expand_param(page) is None:
            break
        page.wait_for_timeout(100)
    assert _expand_param(page) is None, 'the final close clears the marker'


def test_back_from_a_page_the_quick_view_led_to_reopens_the_chart(page):
    _open_expanded(page)
    _to_quick_view(page)
    manage = page.locator('#projectDetailsModal a:has-text("Manage Project")')
    if manage.count() == 0:
        pytest.skip('the quick-view offers no Manage Project link to this user')
    manage.first.click()
    page.wait_for_load_state('networkidle')
    page.go_back()
    page.locator('#chartExpandModal.show').wait_for(timeout=30_000)
    page.locator('#chartExpandModalBody svg').wait_for(timeout=30_000)
