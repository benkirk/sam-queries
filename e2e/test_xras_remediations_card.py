"""The XRAS Remediations card, in a real browser.

Complements `tests/unit/test_xras_remediations.py`: those assert what the
response body carries, this asserts the card renders into its host page, that
adding it did not turn the three-pane worklist into four, and that its chips
filter without a page reload.

⚠️ **The card is legitimately empty on a fresh stack.** It renders whatever
`xras_sweep` last published to the `requests_index` cache key, and a local
stack has usually published nothing. So every content assertion is guarded on
rows being present — an empty stack must not produce a red build for a correct
card. Populate with::

    docker compose exec webdev sam-admin tasks --run xras_sweep --force

⚠️ Nothing here asserts on a username, an email, a request number or a count.
The sweep reads **production XRAS**, so anything this card shows locally is
real people's data and this file is committed.
"""

from __future__ import annotations

import pytest

CARD = '#alloc-xras-remediations'
TABS = '#xrasWorklistTabs'


def _load(page):
    """Open the XRAS page and wait for the remediations fragment to swap in.

    `state='attached'`: the card sits below the tab content, so it is in the
    document as soon as its fragment loads, whatever pane is showing.
    """
    response = page.goto('/allocations/xras')
    assert response is not None and response.status == 200
    page.wait_for_selector(f'{CARD} .card', state='attached', timeout=30_000)
    return page.locator(CARD)


def _rows(card):
    """The card's own request rows.

    Scoped to the outer table and excluding both the collapsed detail rows and
    the opportunity group headers — the nested roster and action tables inside
    an expansion would otherwise be counted as requests.
    """
    return card.locator(
        '> .card > .card-body > .table-responsive > table > tbody '
        '> tr:not(.collapse):not(.table-light)')


class TestItRenders:

    def test_the_card_loads_into_the_page(self, page):
        card = _load(page)
        assert card.locator('.card-header').inner_text().strip()

    def test_the_worklist_still_has_exactly_three_tabs(self, page):
        """⚠️ This is a CARD, not a fourth tab. Adding one would change the
        meaning of every persisted `tab:xrasWorklistTabs` preference."""
        _load(page)
        assert page.locator(f'{TABS} button[data-bs-toggle="tab"]').count() == 3

    def test_it_sits_below_the_tab_content_not_inside_it(self, page):
        _load(page)
        assert page.locator(f'.tab-content {CARD}').count() == 0

    def test_the_warning_about_production_writes_is_present(self, page):
        """Said once, at the top. If rows render, the warning must too."""
        card = _load(page)
        if _rows(card).count() == 0:
            pytest.skip('no swept requests on this stack')
        assert 'production XRAS' in card.inner_text()

    def test_it_loads_without_errors(self, page, errors):
        """The card ships no JavaScript of its own — nothing may throw, and
        its lazy fragment must not fail its htmx swap."""
        _load(page)
        page.wait_for_timeout(500)
        assert not errors.console, errors.console
        assert not errors.exceptions, errors.exceptions
        assert not errors.htmx(), errors.htmx()


class TestInteraction:

    def test_expanding_a_row_reveals_its_roster_and_actions(self, page):
        card = _load(page)
        rows = _rows(card)
        if rows.count() == 0:
            pytest.skip('no swept requests on this stack')

        rows.first.click()
        page.wait_for_timeout(400)
        expansion = card.locator('tr.collapse.show').first
        assert expansion.count() == 1
        text = expansion.inner_text()
        assert 'Roster' in text and 'Actions' in text

    def test_a_status_chip_filters_without_a_page_reload(self, page):
        card = _load(page)
        chips = card.locator('.facet-chip')
        if chips.count() == 0 or _rows(card).count() == 0:
            pytest.skip('nothing to filter on this stack')

        before = _rows(card).count()
        page.evaluate('() => { window.__remediationMarker = 1; }')
        chips.first.click()
        page.wait_for_timeout(700)

        # Still the same document — an htmx swap, not a navigation. A chip that
        # reloaded the page would clear the marker, and would also lose the
        # operator's place in a card they scrolled to.
        assert page.evaluate('() => window.__remediationMarker') == 1
        assert _rows(card).count() <= before
