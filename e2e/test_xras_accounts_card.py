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
PENDING_CARD = '#alloc-xras-pending-requests'

#: Panes, and the tab button that reveals each.
PANES = {CARD: '#xras-pane-accounts', PENDING_CARD: '#xras-pane-pending'}


def _load(page, card=CARD):
    """Open the XRAS page, wait for the fragment, and reveal its tab.

    ⚠️ `state='attached'`, not the default visible: every worklist pane but
    the first is inside an inactive `.tab-pane`, so its card resolves while
    hidden. Waiting for visibility here would time out on a card that had
    already loaded perfectly well.
    """
    response = page.goto('/allocations/xras')
    assert response is not None and response.status == 200
    page.wait_for_selector(f'{card} .card', state='attached', timeout=30_000)
    page.click(f'button[data-bs-target="{PANES[card]}"]')
    page.wait_for_timeout(400)
    return page.locator(card)


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
    for form_id in ('#xras-accounts-filters', '#xras-window-filters'):
        assert page.locator(form_id).count() == 1
        # It must NOT be a descendant of the swap target.
        assert page.locator(f'{CARD} {form_id}').count() == 0


def test_the_three_tabs_share_one_window_control(page):
    """One control, one date pair, three panes. Rendering the pills per tab
    would put three same-named pairs in one form and `form.elements[name]`
    would become a RadioNodeList that set-filter-submit cannot assign to."""
    _load(page)
    assert page.locator('#xrasWorklistTabs button[role="tab"]').count() == 3
    assert page.locator('#xras-window-filters input[name="days"]').count() == 1
    assert page.locator('input[name="start_date"][form="xras-window-filters"]'
                        ).count() == 1
    # Each pane listens for the shared form's submit, so one control moves all.
    for pane_target in ('#alloc-xras-pending', CARD, PENDING_CARD):
        trigger = page.locator(pane_target).get_attribute('hx-trigger')
        assert 'submit from:#xras-window-filters' in trigger


def test_the_request_is_a_column_and_a_chip(page):
    """The handle an operator working one project's activation navigates by.

    ⚠️ Guarded on rows, like every other content assertion here. An empty card
    renders no `<table>` at all, so `thead.inner_text()` does not fail fast —
    it waits out the full 30s timeout and reds the build for a *correct* card.
    That is exactly what it did on the CI stack, whose action log is empty.
    """
    card = _load(page)
    if _rows(card).count() == 0:
        pytest.skip('worklist is empty on this stack; no table to inspect')

    # `.first`: each expansion row carries its own per-action table, so a bare
    # `thead` locator is a strict-mode violation the moment a row renders.
    header = card.locator('thead').first.inner_text().lower()
    assert 'request' in header
    assert card.locator('.facet-grid-label', has_text='Request').count() == 1


def test_the_row_icons_are_gone(page):
    """Per-row glyphs repeated what the text already said — the `placeholder`
    tell is a text badge for exactly that reason. The two remaining
    `fa-circle-info` marks sit on muted notices, not on rows.

    ⚠️ The chevron is the one deliberate exception, and it is not decoration:
    it is the row's expand affordance, the house one (`.collapse-icon`), and
    the only thing that announces the row can be opened at all. Excluded by
    class rather than loosening the count, so a genuine glyph creeping back
    onto a row still fails.

    ⚠️ Scoped to the SUMMARY rows (`tr:not(.collapse)`): the row EXPANSION
    legitimately carries a `fa-triangle-exclamation` on the stuck-placeholder
    merge notice — an actionable alert, not a per-row glyph — which only appears
    when the swept data actually holds such a placeholder (so this passed on an
    empty CI stack and failed the moment a real sweep surfaced one). The rule is
    "no decoration on the rows themselves"; a notice inside an opened panel is
    exactly the muted-notice case the docstring already allows.
    """
    card = _load(page)
    assert card.locator(
        'tbody tr:not(.collapse) i.fas:not(.collapse-icon)').count() == 0


def test_the_pending_requests_tab_states_are_distinct(page):
    """Feed B has three empty states and conflating them would mislead:
    unconfigured, no snapshot published, and published-but-empty."""
    card = _load(page, PENDING_CARD)
    body = card.inner_text().lower()
    rows = card.locator(
        '> .card > .table-responsive > table > tbody > tr').count()
    if rows:
        # A published snapshot must say when it was swept — the tab is only
        # ever as fresh as the last sweep and must not imply live data.
        assert 'swept' in body
    else:
        assert ('not configured' in body or 'no sweep has published' in body
                or 'has a sam project' in body)


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
    assert 'New account' in text
    assert 'Reactivation' in text


def test_a_chip_filters_without_a_page_load(page):
    """The chips write into the hidden form and re-submit it via htmx."""
    card = _load(page)
    if _rows(card).count() == 0:
        pytest.skip('worklist is empty on this stack; nothing to filter')

    before = _rows(card).count()
    page.once('load', lambda _: pytest.fail('the chip triggered a full reload'))
    card.locator('button.facet-chip', has_text='New account').first.click()
    page.wait_for_timeout(1200)

    card = page.locator(CARD)
    after = _rows(card).count()
    assert after <= before
    assert card.locator('button.facet-chip.is-active').count() >= 1


def test_a_row_expands_to_its_actions(page):
    """⚠️ The toggle is on the row's CELLS, not the <tr>.

    It used to be on the <tr>, which was safe only while the row carried no
    buttons. The username is now a link when SAM has the account, and
    Bootstrap's collapse data-api runs in the capture phase — an ancestor
    toggle would fire before the link's own handler and flip the row open
    behind the modal. See dashboards/fragments/collapse.html.
    """
    card = _load(page)
    if _rows(card).count() == 0:
        pytest.skip('worklist is empty on this stack; nothing to expand')

    first = _rows(card).first
    trigger = first.locator('[data-bs-toggle="collapse"]').first
    body_id = trigger.get_attribute('data-bs-target')
    assert body_id, 'no cell in the row carries a collapse target'
    panel = page.locator(body_id)
    assert not panel.is_visible()
    trigger.click()
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


class TestTheUsernameLink:
    """A username SAM already knows opens the shared user modal.

    ⚠️ Guarded on a link existing. Only `inactive` rows link — a `users` row
    that exists and is deactivated — and on a fresh snapshot every row is
    `absent` instead, which is the healthy shape. An empty stack must not be a
    red build.
    """

    def test_it_opens_the_user_modal_without_expanding_the_row(self, page):
        card = _load(page)
        link = card.locator('a[data-bs-target="#userDetailsModal"]')
        if link.count() == 0:
            pytest.skip('no Reactivation row on this stack')

        open_before = card.locator('tr.collapse.show').count()
        link.first.click()
        page.wait_for_timeout(1200)
        assert page.locator('#userDetailsModal.show').count() == 1
        # ⚠️ The capture-phase half: an ancestor toggle would have flipped the
        # row open behind the modal. See fragments/collapse.html.
        assert card.locator('tr.collapse.show').count() == open_before

    def test_the_chevron_still_expands_the_row(self, page):
        """The chevron kept its place at the start of the row and took its own
        trigger, the username cell having none."""
        card = _load(page)
        rows = _rows(card)
        if rows.count() == 0:
            pytest.skip('no accounts needed on this stack')

        icon = rows.first.locator('.collapse-icon')
        assert icon.count() == 1
        flat = icon.evaluate('e => getComputedStyle(e).transform')
        icon.click()
        page.wait_for_timeout(500)
        assert card.locator('tr.collapse.show').count() >= 1
        assert icon.evaluate('e => getComputedStyle(e).transform') != flat
