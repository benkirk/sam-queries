"""The expanded chart is a view dialog (modal-history.js): a quick-view opened from it
steps back to it, on its close button and on Back, and Forward walks the chain again.

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


def _shown(page):
    page.wait_for_timeout(1200)
    return page.evaluate("[...document.querySelectorAll('.modal.show')].map(m => m.id)")


def _chart_drawn(page):
    page.locator('#chartExpandModalBody svg').first.wait_for(timeout=30_000)


def _chart_then_quick_view(page):
    visit(page, '/user/accounts')
    visit(page, ROUTE)
    opener = page.locator('[data-chart-expand-opener]:visible').first
    if opener.count() == 0:
        pytest.skip('no expandable chart in this dataset')
    opener.click()
    page.locator('#chartExpandModal.show').wait_for(timeout=10_000)
    page.locator('#chartExpandModalBody svg a').first.wait_for(state='attached', timeout=30_000)
    page.wait_for_timeout(600)   # Bootstrap ignores hide() during the open transition
    if not page.evaluate(_PROJECT_WEDGE):
        pytest.skip('the expanded chart names no project')
    page.locator('#projectDetailsModal.show').wait_for(timeout=10_000)
    page.wait_for_timeout(600)


def test_closing_the_quick_view_returns_to_the_chart(page):
    _chart_then_quick_view(page)
    page.locator('#projectDetailsModal .btn-close').click()
    assert _shown(page) == ['chartExpandModal']
    _chart_drawn(page)
    page.locator('#chartExpandModal .btn-close').click()
    assert _shown(page) == []


def test_back_and_forward_walk_the_chain(page):
    _chart_then_quick_view(page)
    page.go_back()
    assert _shown(page) == ['chartExpandModal']
    _chart_drawn(page)
    page.go_back()
    assert _shown(page) == [] and page.url.endswith(ROUTE)
    page.go_forward()
    assert _shown(page) == ['chartExpandModal']
    page.go_forward()
    assert _shown(page) == ['projectDetailsModal']


def test_back_from_a_page_the_quick_view_led_to_walks_back_through_the_chart(page):
    _chart_then_quick_view(page)
    manage = page.locator('#projectDetailsModal a:has-text("Manage Project")')
    if manage.count() == 0:
        pytest.skip('the quick-view offers no Manage Project link to this user')
    manage.first.click()
    page.wait_for_load_state('networkidle')
    page.go_back()
    page.wait_for_load_state('networkidle')
    assert _shown(page) == ['projectDetailsModal']
    page.go_back()
    assert _shown(page) == ['chartExpandModal']
    _chart_drawn(page)
