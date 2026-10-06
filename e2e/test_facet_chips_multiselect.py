"""Facet chips are multi-select toggles.

Clicking a chip adds its value to the dimension's hidden ``<select multiple>``
and re-submits the filter form; clicking a pressed chip removes it. Only a
browser proves the click handler (``set-filter-submit`` in ``actions.js``)
really keeps the other selections — the server tier can only show that two
values in the query string render as two pressed chips.

Driven on the notification log because its Status strip renders its whole
vocabulary, including at zero, so the test needs no particular rows.
"""

from playwright.sync_api import expect

from conftest import visit

PAGE = '/admin/htmx/notifications'


def _chip(page, value):
    return page.locator(
        f'button.facet-chip[data-field="status"][data-value="{value}"]')


def test_chips_accumulate_and_toggle_off(page):
    visit(page, PAGE)
    expect(_chip(page, 'sent')).to_have_attribute('aria-pressed', 'false')

    _chip(page, 'sent').click()
    expect(_chip(page, 'sent')).to_have_attribute('aria-pressed', 'true')

    _chip(page, 'failed').click()
    expect(_chip(page, 'failed')).to_have_attribute('aria-pressed', 'true')
    expect(_chip(page, 'sent')).to_have_attribute('aria-pressed', 'true')

    _chip(page, 'sent').click()
    expect(_chip(page, 'sent')).to_have_attribute('aria-pressed', 'false')
    expect(_chip(page, 'failed')).to_have_attribute('aria-pressed', 'true')
