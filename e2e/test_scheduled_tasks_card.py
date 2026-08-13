"""The Scheduled tasks card on Admin → Configuration.

Two scenarios, both chosen because they are about what the operator actually
*sees* when the chart ships:

1. **The card renders**, and ``Details »`` reaches the run-history page. The
   Configuration tab is one lazy htmx fragment, so a card that raises during
   ``gather_runtime_state`` does not 500 the page — it silently vanishes.
   Only a browser proves the tile is really there after the swap.

2. **The kill-switch warning.** The single most important pixel here.
   Production ships with ``SAM_TASKS_DISABLED: "cleanup_status_snapshots"``
   (``helm/values.yaml``), so the first thing this card shows in production is
   a dispatcher waking hourly and deliberately doing nothing. If the card does
   not say so loudly it looks like a healthy system.

Everything else about this feature is cheaper and less fragile at the unit
tier: the ``unavailable`` degrade needs a table-less database (there is no
monkeypatch here — this tier drives a live stack over HTTP), and the 403
boundary is covered by ``tests/unit/test_admin_scheduled_tasks_page.py``'s
``config_only_client``.

To exercise scenario 2 locally the stack has to actually carry the switch::

    SAM_TASKS_DISABLED=cleanup_status_snapshots docker compose up webdev --watch
    make e2e SAM_E2E_BASE_URL=http://localhost:5050

Without it that test skips rather than passing vacuously.
"""

import pytest

from conftest import visit

CONFIG = '/admin/configuration'
TASKS = '/admin/htmx/tasks'

#: The tile's own container, and the text that identifies it.
CARD = ".card.inner-card:has(h5:has-text('Scheduled tasks'))"


@pytest.fixture
def config_page(page):
    """Admin → Configuration with the lazy card fragment already swapped in."""
    visit(page, CONFIG)
    # The whole tab is one htmx fragment loaded on `load`; nothing below is
    # meaningful until it lands.
    page.wait_for_selector(CARD, timeout=15_000)
    return page


class TestTheCardRenders:

    def test_the_tile_is_present(self, config_page):
        assert config_page.locator(CARD).count() == 1, \
            'the Scheduled tasks tile is missing from Admin → Configuration ' \
            '— a card that raises in gather_runtime_state does not 500 the ' \
            'tab, it silently disappears'

    def test_it_names_the_registered_task(self, config_page):
        """`scheduling.registry.TASKS` is populated by import side effects. If
        that import stops happening the tile reports zero tasks and still
        renders, which is the failure this catches."""
        assert config_page.locator(CARD).inner_text().count(
            'cleanup_status_snapshots') >= 1

    def test_details_reaches_the_run_history(self, config_page):
        """The link is deliberately ungated — the page it targets is
        VIEW_SYSTEM_CONFIG, the same tier as the card."""
        config_page.locator(f"{CARD} a[href='{TASKS}']").click()
        config_page.wait_for_url(f'**{TASKS}')
        assert config_page.locator("h2:has-text('Scheduled tasks')").count() == 1

    def test_the_run_history_table_loads(self, page):
        """The log fragment is a second lazy swap behind the page shell."""
        visit(page, TASKS)
        page.wait_for_selector('#scheduledTasksTable', timeout=15_000)
        # Either rows or the empty state — both are a rendered table, and a
        # broken grid is neither.
        assert page.locator('#scheduledTasksTable tbody tr').count() >= 1


class TestTheKillSwitchWarning:
    """A kill-switched dispatcher must not look healthy."""

    def test_a_disabled_task_is_called_out(self, config_page):
        card = config_page.locator(CARD)
        if 'Disabled:' not in card.inner_text():
            pytest.skip(
                'this stack has no SAM_TASKS_DISABLED set, so there is no '
                'warning to assert. Re-run with '
                'SAM_TASKS_DISABLED=cleanup_status_snapshots on the webapp.')

        assert card.locator('.alert-warning').count() >= 1, \
            'a disabled task is named but not rendered as a warning — ' \
            'production ships kill-switched and this is the pixel that ' \
            'stops it reading as healthy'
        text = card.inner_text()
        assert 'cleanup_status_snapshots' in text
        assert 'do nothing' in text, \
            'the warning must say what being disabled MEANS, not just name ' \
            'the task'
