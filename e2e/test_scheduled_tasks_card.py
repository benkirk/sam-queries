"""The Scheduled tasks card on Admin -> Configuration.

Two scenarios, both chosen because they are about what the operator actually
*sees* when the chart ships:

1. **The card renders**, and ``Details »`` reaches the run-history page. The
   Configuration tab is one lazy htmx fragment, so a card that raises during
   ``gather_runtime_state`` does not 500 the page — it silently vanishes.
   Only a browser proves the tile is really there after the swap.

2. **The kill-switch warning.** The single most important pixel here.
   Production ships ``SAM_TASKS_DISABLED`` non-empty (``helm/values.yaml``,
   currently the two tasks awaiting review), so the card always has a
   dispatcher waking hourly and deliberately doing nothing to report. If it
   does not say so loudly the system looks healthy.

   The assertions below deliberately do **not** name a task: which tasks are
   switched off is a chart decision that changes without this file, and a
   pinned name turns that ordinary edit into a red e2e run. What is asserted
   is the shape — a warning, and an explanation of what disabled *means*.

Everything else about this feature is cheaper and less fragile at the unit
tier: the ``unavailable`` degrade needs a table-less database (there is no
monkeypatch here — this tier drives a live stack over HTTP), and the 403
boundary is covered by ``tests/unit/test_admin_scheduled_tasks_page.py``'s
``config_only_client``.

To exercise scenario 2 locally the stack has to actually carry the switch.
``compose.yaml`` passes the host variable through (empty by default), so::

    SAM_TASKS_DISABLED=expiration_notices docker compose up webdev --watch
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
    """Admin -> Configuration with the lazy card fragment already swapped in."""
    visit(page, CONFIG)
    # The whole tab is one htmx fragment loaded on `load`; nothing below is
    # meaningful until it lands.
    page.wait_for_selector(CARD, timeout=15_000)
    return page


def _ledger_present(config_page):
    """Whether this stack has `task_run` — i.e. whether Alembic 0006 ran.

    **Not a convenience.** CI's status database genuinely has no `task_run`,
    and so do staging and production until the migration is applied. The
    degraded state is a real shipping state, not a test artifact, so both
    branches below are assertions rather than one being a skip.
    """
    return 'unavailable' not in config_page.locator(CARD).inner_text().lower()


class TestTheCardRenders:

    def test_the_tile_is_present(self, config_page):
        assert config_page.locator(CARD).count() == 1, \
            'the Scheduled tasks tile is missing from Admin → Configuration ' \
            '— a card that raises in gather_runtime_state does not 500 the ' \
            'tab, it silently disappears'

    def test_it_says_something_useful_either_way(self, config_page):
        """With the ledger: names the registered task, which proves
        `scheduling.registry.TASKS` was populated by its import side effects.
        Without it: says so, rather than rendering an empty healthy-looking
        card."""
        text = config_page.locator(CARD).inner_text()
        if _ledger_present(config_page):
            assert 'cleanup_status_snapshots' in text
        else:
            assert 'task_run' in text and 'unavailable' in text.lower()

    def test_details_is_offered_only_when_there_is_data(self, config_page):
        """The link is ungated on permission — the page is VIEW_SYSTEM_CONFIG,
        the same tier as the card — but suppressed when the ledger is absent,
        because offering a link to a page with nothing to draw is the same
        discourtesy as offering one that 403s."""
        link = config_page.locator(f"{CARD} a[href='{TASKS}']")
        assert link.count() == (1 if _ledger_present(config_page) else 0)

    def test_the_run_history_page_never_500s(self, page, config_page):
        """The regression CI caught: the card degraded correctly while the
        page it links to threw a 500, because only the card had a fallback.

        `visit` asserts HTTP 200, so reaching it at all is the assertion."""
        present = _ledger_present(config_page)
        visit(page, TASKS)

        if present:
            page.wait_for_selector('#scheduledTasksTable', timeout=15_000)
            # Rows or the empty state — both are a rendered table, and a
            # broken grid is neither.
            assert page.locator('#scheduledTasksTable tbody tr').count() >= 1
        else:
            assert 'unavailable' in page.locator('body').inner_text().lower()


class TestTheKillSwitchWarning:
    """A kill-switched dispatcher must not look healthy."""

    def test_a_disabled_task_is_called_out(self, config_page):
        if not _ledger_present(config_page):
            pytest.skip('no task_run on this stack, so the card is in its '
                        'degraded state and shows no task list at all')
        card = config_page.locator(CARD)
        if 'Disabled:' not in card.inner_text():
            pytest.skip(
                'this stack has no SAM_TASKS_DISABLED set, so there is no '
                'warning to assert. Re-run with e.g. '
                'SAM_TASKS_DISABLED=expiration_notices on the webapp.')

        assert card.locator('.alert-warning').count() >= 1, \
            'a disabled task is named but not rendered as a warning — ' \
            'production ships kill-switched and this is the pixel that ' \
            'stops it reading as healthy'
        text = card.inner_text()
        # Shape, not identity: assert the warning actually NAMES something,
        # rather than pinning whichever tasks the chart switches off today.
        named = text.split('Disabled:', 1)[1].lstrip()
        assert named[:1].isalpha(), \
            f'the warning says "Disabled:" but names no task — got {named[:60]!r}'
        assert 'do nothing' in text, \
            'the warning must say what being disabled MEANS, not just name ' \
            'the task'
