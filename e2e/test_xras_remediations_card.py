"""The XRAS Remediations card, in a real browser.

Complements `tests/unit/test_xras_remediations.py`: those assert what the
response body carries, this asserts the card renders into its host page, that
adding it did not turn the three-pane worklist into four, and that its chips
filter without a page reload.

WARNING: **The card is legitimately empty on a fresh stack.** It renders whatever
`xras_sweep` last published to the `requests_index` cache key, and a local
stack has usually published nothing. So every content assertion is guarded on
rows being present — an empty stack must not produce a red build for a correct
card. Populate with::

    docker compose exec webdev sam-admin tasks --run xras_sweep --force

WARNING: Nothing here asserts on a username, an email, a request number or a count.
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
        '> tr:not(.collapse):not(.table-subtle)')


class TestItRenders:

    def test_the_card_loads_into_the_page(self, page):
        card = _load(page)
        assert card.locator('.card-header').inner_text().strip()

    def test_the_worklist_still_has_exactly_two_tabs(self, page):
        """WARNING: This is a CARD, not a third tab. Adding one would change the
        meaning of every persisted `tab:xrasWorklistTabs` preference."""
        _load(page)
        assert page.locator(f'{TABS} button[data-bs-toggle="tab"]').count() == 2

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

    def test_the_request_link_opens_the_detail_modal_with_roster_and_actions(
            self, page):
        """WARNING: The per-request row expansion is GONE — PR #464 folded the roster
        and actions into the read-only detail modal, and the Request number is
        now the single entry point. So the roster/actions live in
        `#auditDetailsModal`, reached by clicking the request link, not by
        expanding the row."""
        card = _load(page)
        rows = _rows(card)
        if rows.count() == 0:
            pytest.skip('no swept requests on this stack')

        link = card.locator('a[hx-get*="xras_request_detail"]').first
        assert link.count() == 1, 'the request has no detail-modal link'
        link.click()
        page.wait_for_selector('#auditDetailsModal.show', timeout=15_000)
        # The detail read is live; on any stack that swept, outgoing is
        # configured, so the modal carries the shared roster/actions strip.
        page.wait_for_timeout(800)
        text = page.locator('#auditDetailsModalBody').inner_text().casefold()
        assert 'roster' in text and 'actions' in text

    def test_the_group_header_chevron_rotates_when_the_group_toggles(self, page):
        """The affordance is pure CSS — `.collapse-icon` rotating off the
        `aria-expanded` Bootstrap writes onto the trigger. Since PR #464 the
        chevron lives on the opportunity GROUP header (`.table-subtle`), and
        since PR #466 that header's toggle is a chevron span (the header's name
        is a link to the opportunity modal, so the toggle could not stay on the
        row). Groups render open, so the chevron starts rotated and a click
        flattens it."""
        card = _load(page)
        if _rows(card).count() == 0:
            pytest.skip('no swept requests on this stack')

        icon = card.locator('tr.table-subtle .collapse-icon').first
        assert icon.count() == 1, 'the group header offers no expand affordance'
        expanded = icon.evaluate('e => getComputedStyle(e).transform')
        icon.click()
        page.wait_for_timeout(400)
        assert icon.evaluate('e => getComputedStyle(e).transform') != expanded

    def test_a_chip_click_carries_the_search_term(self, page):
        """WARNING: The whole point of `form=` on the search input. The chip submits
        the hidden filter form; the input lives inside the card, two elements
        away. Without the attribute the term is not in that form's data and
        every chip click silently clears the search."""
        card = _load(page)
        box = card.locator('#xras-remediation-search')
        chips = card.locator('.facet-chip')
        if chips.count() == 0 or _rows(card).count() == 0:
            pytest.skip('nothing to filter on this stack')

        box.fill('a')
        page.wait_for_timeout(700)
        if card.locator('.facet-chip').count() == 0:
            pytest.skip('the probe term matched nothing on this stack')

        card.locator('.facet-chip').first.click()
        page.wait_for_timeout(700)
        assert card.locator('#xras-remediation-search').input_value() == 'a'

    def test_the_search_box_survives_matching_nothing(self, page):
        """Otherwise the only way out of a typo is a page reload."""
        card = _load(page)
        if _rows(card).count() == 0:
            pytest.skip('no swept requests on this stack')

        card.locator('#xras-remediation-search').fill('zzz-matches-nothing')
        page.wait_for_timeout(700)
        assert _rows(card).count() == 0
        assert card.locator('#xras-remediation-search').input_value() \
            == 'zzz-matches-nothing'

    def test_the_project_badge_opens_the_modal_without_expanding_the_row(
            self, page):
        """WARNING: Both halves matter, and the second is the whole reason the
        toggle moved off the `<tr>`. Bootstrap's collapse data-api runs in the
        CAPTURE phase, so an ancestor toggle fires before the link's handler —
        the modal would open AND the row would flip open behind it, on every
        click, with no button-side guard able to stop it."""
        card = _load(page)

        # WARNING: Widen the shared window first. A request SAM already has a project
        # for is by definition one that got as far as a handoff, so it skews
        # OLD — with the default lookback this test skipped on every stack,
        # including the one it was written against, and proved nothing.
        page.evaluate("""() => {
            const f = document.querySelector('#xras-window-filters');
            f.querySelector('[name=days]').value = '3650';
            htmx.trigger(f, 'submit');
        }""")
        page.wait_for_timeout(2500)

        link = card.locator('a[data-bs-target="#projectDetailsModal"]')
        if link.count() == 0:
            pytest.skip('no swept request has a SAM project on this stack')

        row = link.first.locator('xpath=ancestor::tr[1]')
        body = page.locator(f'#{row.get_attribute("id") or ""}')
        expansions_open_before = card.locator('tr.collapse.show').count()

        link.first.click()
        page.wait_for_timeout(1200)
        assert page.locator('#projectDetailsModal.show').count() == 1
        assert card.locator('tr.collapse.show').count() == expansions_open_before

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


class TestContractStrip:
    """The contract-blockers strip: each number opens Admin -> Contracts with the
    New Contract modal already showing and seeded. Guarded like the rest of this
    file — a stack whose sweep found no contract blocker skips, and nothing here
    asserts on a number, a name, or a count."""

    STRIP = f'{CARD} #xras-contract-strip'

    def test_each_number_links_to_a_seeded_create(self, page):
        _load(page)
        strip = page.locator(self.STRIP)
        if strip.count() == 0:
            pytest.skip('no contract blocker on this stack')
        links = strip.locator('a[href*="create="]')
        if links.count() == 0:
            pytest.skip('only spelling variants on this stack')
        for i in range(links.count()):
            assert '/admin/contracts?' in links.nth(i).get_attribute('href')

    def test_following_a_link_opens_the_modal_seeded(self, page):
        _load(page)
        links = page.locator(f'{self.STRIP} a[href*="create="]')
        if links.count() == 0:
            pytest.skip('no contract blocker on this stack')
        href = links.first.get_attribute('href')
        response = page.goto(href)
        assert response is not None and response.status == 200
        assert page.locator('[data-auto-open-create]').count() == 1
        page.wait_for_selector('#createContractModal.show', timeout=15_000)
        page.wait_for_selector('#createContractModal #createContractNumber', timeout=15_000)
        assert page.locator('#createContractModal #createContractNumber').input_value().strip()


class TestIdentityStrip:
    """The identity strip: each placeholder opens the merge modal in place.
    Guarded like the rest of this file, and asserting on structure only —
    never a username, an email, or a count."""

    STRIP = f'{CARD} #xras-identity-strip'

    def test_each_ready_placeholder_opens_the_merge_modal(self, page):
        _load(page)
        strip = page.locator(self.STRIP)
        if strip.count() == 0:
            pytest.skip('no placeholder blocker on this stack')
        assert '@' not in strip.inner_text()
        buttons = strip.locator('button[hx-get*="/allocations/xras_merge_form/"]')
        if buttons.count() == 0:
            pytest.skip('nothing ready to merge on this stack')
        assert buttons.first.get_attribute('data-bs-target') == '#auditDetailsModal'


class TestPendingWorkToggle:

    def test_show_everything_reloads_the_card(self, page):
        card = _load(page)
        switch = card.locator('#xras-remediation-show-all')
        if switch.count() == 0:
            # The controls render only once something was swept; CI and a
            # fresh stack have published nothing.
            pytest.skip('no swept requests on this stack')
        assert not switch.is_checked()
        switch.check()
        page.wait_for_selector(f'{CARD} #xras-remediation-show-all:checked', timeout=15_000)
        assert page.locator(f'{CARD} .card').count() == 1


class TestSortKeepsScroll:

    def test_a_header_click_does_not_jump_to_the_top(self, page):
        """The sort header is an <a href="#"> driven by data-action; the handler
        must preventDefault or the swap is followed by a jump to the top."""
        card = _load(page)
        header = card.locator('th a[data-action="set-sort-submit"]').first
        if header.count() == 0:
            pytest.skip('no sortable rows on this stack')
        header.scroll_into_view_if_needed()
        before = page.evaluate('() => window.scrollY')
        if before < 200:
            pytest.skip('card sits at the top of the viewport on this stack')
        header.click()
        page.wait_for_timeout(1500)          # the swap, then any hash scroll
        after = page.evaluate('() => window.scrollY')
        assert after > 200, f'scrolled from {before} to {after}'
