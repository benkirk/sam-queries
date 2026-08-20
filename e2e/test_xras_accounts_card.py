"""The XRAS account-creation worklist card, in a real browser.

Complements the HTTP-tier tests in `tests/unit/test_xras_accounts_card.py`:
those assert what the response body carries, this asserts the card actually
renders, its lazy fragment loads, and its chips filter without a page reload.

⚠️ **Needs a populated `xras_action_log`.** The card is legitimately empty on
a fresh stack (see the module docstring in `sam/queries/xras_accounts.py`), so
every content assertion here is guarded on rows being present rather than
asserted unconditionally — an empty stack must not produce a red build for a
correct card. Seed with::

    python scripts/xras/seed_dev_actions.py --dir ~/xras_payloads_raw

Nothing here asserts on a username, an email or a count: the local corpus is
unscrubbed, and this file is committed.
"""

from __future__ import annotations

import pytest

CARD = '#alloc-xras-accounts'
FRAGMENT_READY = f'{CARD} .card'


def _load(page):
    """Open the XRAS page and wait for the lazily-fetched accounts fragment."""
    response = page.goto('/allocations/xras')
    assert response is not None and response.status == 200
    page.wait_for_selector(FRAGMENT_READY, timeout=30_000)
    return page.locator(CARD)


def _rows(card):
    """The worklist's own rows.

    ⚠️ Scoped to the OUTER table on purpose. A loose `tbody > tr:not(.collapse)`
    also matches the nested per-action table inside each expansion row — it
    reported 19 where the card shows 9 — which would have made the
    before/after comparison in the chip test meaningless.
    """
    return card.locator(
        '> .card > .table-responsive > table > tbody > tr:not(.collapse)')


def _badge_count(card):
    """What the header claims, as an independent check on the row count."""
    return int(card.locator('.card-header .badge').first.inner_text().strip())


def test_the_card_renders_its_own_fragment(page):
    """The container is empty in the shell and filled by htmx on load."""
    card = _load(page)
    assert card.locator('.card-header').inner_text().strip()
    assert 'Accounts Needed' in card.locator('.card-header').inner_text()


def test_the_filter_form_is_outside_the_fragment(page):
    """Controls inside the fragment would vanish with the empty state, and
    the container refetches a bare hx-get on refreshXrasTab."""
    _load(page)
    form = page.locator('#xras-accounts-filters')
    assert form.count() == 1
    # It must NOT be a descendant of the swap target.
    assert page.locator(f'{CARD} #xras-accounts-filters').count() == 0


def test_the_header_count_matches_the_rows_drawn(page):
    """Two independent renderings of the same number; a mismatch means the
    route filtered after counting (or vice versa)."""
    card = _load(page)
    assert _badge_count(card) == _rows(card).count()


def test_both_classification_chips_render(page):
    """Even at zero — an absent chip reads as 'not measured', which is a
    different claim from 'none'."""
    card = _load(page)
    text = card.inner_text()
    assert 'Create account' in text
    assert 'Reactivate account' in text


def test_a_chip_filters_without_a_page_load(page):
    """The chips write into the hidden form and re-submit it via htmx."""
    card = _load(page)
    if _rows(card).count() == 0:
        pytest.skip('worklist is empty on this stack; nothing to filter')

    before = _rows(card).count()
    page.once('load', lambda _: pytest.fail('the chip triggered a full reload'))
    card.locator('button.facet-chip', has_text='Create account').first.click()
    page.wait_for_timeout(1200)

    card = page.locator(CARD)
    after = _rows(card).count()
    assert after <= before
    assert card.locator('button.facet-chip.is-active').count() >= 1


def test_a_row_expands_to_its_actions(page):
    """The toggle is on the <tr>, which is only safe because the row carries
    no buttons — see dashboards/fragments/collapse.html."""
    card = _load(page)
    if _rows(card).count() == 0:
        pytest.skip('worklist is empty on this stack; nothing to expand')

    first = _rows(card).first
    body_id = first.get_attribute('data-bs-target')
    assert body_id, 'the row carries no collapse target'
    panel = page.locator(body_id)
    assert not panel.is_visible()
    first.click()
    page.wait_for_timeout(600)
    assert panel.is_visible(), 'the row did not expand'
    # The expansion lists the actions that named this username. Compared
    # case-insensitively: the table-subtle header is uppercased by CSS, so
    # inner_text() returns what is PAINTED, not what the template wrote.
    assert 'request' in panel.inner_text().lower()


def test_the_card_logs_no_console_errors(page):
    """Same contract as the console sweep: a card that renders but throws is
    not working."""
    errors = []
    page.on('console', lambda m: errors.append(m.text)
            if m.type == 'error' else None)
    page.on('pageerror', lambda e: errors.append(str(e)))
    card = _load(page)
    if _rows(card).count():
        _rows(card).first.click()
        page.wait_for_timeout(600)
    assert not errors, f'console errors on the XRAS page: {errors}'
