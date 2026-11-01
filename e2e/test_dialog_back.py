"""Back and Forward walk the view dialogs (modal-history.js).

Back closes the open dialog, Forward reopens it, its close button steps back, and a reload or
a return to the page shows the dialog that was open. A form opened from a view dialog takes
no entry and returns to the view dialog when it closes.
"""
import pytest

from conftest import visit

ROUTE = '/user/accounts'
PREVIOUS = '/allocations/transactions'
_OPENER = """() => { const card = document.querySelector('[data-projcode]');
    if (!card) return false;
    const url = '/user/project-details-modal/' + card.dataset.projcode;
    const l = document.createElement('a'); l.href = '#'; l.id = 'qvLink'; l.textContent = 'project';
    l.dataset.action = 'show-detail-modal'; l.dataset.modalId = 'projectDetailsModal';
    l.setAttribute('hx-get', url); l.setAttribute('hx-target', '#projectDetailsModalBody');
    document.body.prepend(l); htmx.process(l); return true; }"""
_FORM = """() => { document.body.insertAdjacentHTML('beforeend', '<div class="modal fade" id="e2eForm" tabindex="-1">'
    + '<div class="modal-dialog"><div class="modal-content"><div class="modal-header"><h5 class="modal-title">Form</h5>'
    + '<button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body">f</div></div></div></div>');
    const b = document.createElement('button'); b.type = 'button'; b.id = 'toForm'; b.textContent = 'edit';
    b.setAttribute('data-bs-toggle', 'modal'); b.setAttribute('data-bs-target', '#e2eForm');
    document.getElementById('projectDetailsModalBody').prepend(b); }"""


def _shown(page):
    page.wait_for_timeout(1200)
    return page.evaluate("[...document.querySelectorAll('.modal.show')].map(m => m.id)")


def _filled(page):
    return page.evaluate("document.getElementById('projectDetailsModalBody').innerText.trim().length > 40")


def _arrive(page):
    visit(page, PREVIOUS)
    visit(page, ROUTE)


def _quick_view(page):
    if not page.evaluate(_OPENER):
        pytest.skip('this user has no project in this dataset')
    page.click('#qvLink')
    page.locator('#projectDetailsModal.show').wait_for(timeout=10_000)
    page.wait_for_timeout(500)


def _back_leaves(page):
    page.go_back()
    page.wait_for_load_state('networkidle')
    assert page.url.endswith(PREVIOUS), 'no entry was left behind'


def test_back_closes_and_forward_reopens(page):
    _arrive(page)
    _quick_view(page)
    page.go_back()
    assert _shown(page) == [] and page.url.endswith(ROUTE)
    page.go_forward()
    assert _shown(page) == ['projectDetailsModal'] and _filled(page)
    page.go_back()
    assert _shown(page) == []
    _back_leaves(page)


def test_the_close_button_steps_back(page):
    _arrive(page)
    _quick_view(page)
    page.locator('#projectDetailsModal .btn-close').click()
    assert _shown(page) == []
    page.go_forward()
    assert _shown(page) == ['projectDetailsModal']
    page.keyboard.press('Escape')
    assert _shown(page) == []
    _back_leaves(page)


def test_returning_to_the_page_shows_the_dialog_you_left_from(page):
    _arrive(page)
    _quick_view(page)
    manage = page.locator('#projectDetailsModal a:has-text("Manage Project")')
    if manage.count() == 0:
        pytest.skip('the quick-view offers no Manage Project link to this user')
    manage.first.click()
    page.wait_for_load_state('networkidle')
    page.go_back()
    page.wait_for_load_state('networkidle')
    assert _shown(page) == ['projectDetailsModal'] and _filled(page)
    page.go_back()
    assert _shown(page) == []
    _back_leaves(page)
    page.go_forward()
    page.wait_for_load_state('networkidle')
    page.go_forward()
    assert _shown(page) == ['projectDetailsModal']
    page.go_forward()
    page.wait_for_load_state('networkidle')
    assert '/admin/project/' in page.url, 'Forward reaches the page the dialog led to'


def test_a_reload_keeps_the_dialog(page):
    _arrive(page)
    _quick_view(page)
    page.reload()
    page.wait_for_load_state('networkidle')
    assert _shown(page) == ['projectDetailsModal'] and _filled(page)
    page.go_back()
    assert _shown(page) == []


def test_a_form_from_a_view_dialog_returns_to_it(page):
    _arrive(page)
    _quick_view(page)
    page.evaluate(_FORM)
    page.click('#toForm')
    assert _shown(page) == ['e2eForm']
    page.locator('#e2eForm .btn-close').click()
    assert _shown(page) == ['projectDetailsModal']
    page.go_back()
    assert _shown(page) == []
    _back_leaves(page)
