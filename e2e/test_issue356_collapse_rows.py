"""Issue #356: an action button must not toggle its collapse-trigger row.

A group row written as ``<tr data-bs-toggle="collapse">`` that also contains an
Edit/Delete button toggles the row when the button is clicked. Bootstrap
registers its data-api handlers via ``EventHandler.on(document, ...)``, which
passes the delegation flag as ``addEventListener``'s ``useCapture`` argument,
so they run in the **capture** phase on ``document`` — before any listener on
the button. Nothing the button does (``data-stop-propagation`` included) can
prevent it; the fix is to move the toggle onto the non-action ``<td>``s.

``tests/unit/test_collapse_trigger_rows.py`` proves the *markup* no longer puts
a trigger on a row that holds a button. Only a browser proves the *behavior*,
and the issue's acceptance criterion is written as one::

    { rowExpandedAfterEditClick: false, modalShown: true }

Both halves matter, hence two tests per card: the pencil must not expand the
row, **and** the ordinary cells must still expand it. A fix that killed the
expander outright would satisfy the first on its own.

Sibling of ``test_pr378_regressions.py`` — a named-issue behavioral module,
as opposed to the console and legibility sweeps.
"""
import pytest

from conftest import visit


#: (label, page route, tab button selector or None, readiness selector,
#:  data-bs-target prefix)
#:
#: The prefix is what ties a case to its card: each fragment names its collapse
#: targets after the entity, so `#aoi-group-` can only match the Areas card.
#: Resources is included as a non-regression case — it was fixed in #355 and
#: its wallclock-exemption pencil lost a (dead) stop_propagation flag here.
#:
#: The readiness selector is load-bearing, not decoration. Several of these
#: panes are htmx fragments — #institutions-pane fetches itself on
#: `shown.bs.tab from:#institutions-tab once` — so clicking the tab and
#: waiting for networkidle can return before the rows exist. Without an
#: explicit wait the "no row-level trigger" assertion passes against an empty
#: pane and the case skips, which is a silent pass on the exact bug this file
#: is here to catch. Observed for real: the institution-type case skipped
#: against a container that definitely still had the bug.
CARDS = [
    ('facility', '/admin/facilities', None,
     '#facilities-pane table', '#facility-panels-'),
    ('institution-type', '/admin/organizations', '#institutions-tab',
     '#institutions-pane table', '#inst-type-'),
    ('aoi-group', '/admin/organizations', '#areas-tab',
     '#areas-pane table', '#aoi-group-'),
    ('contract-source', '/admin/contracts', None,
     '#contractsTable', '#contract-source-'),
    ('resource-type', '/admin/resources', None,
     '#resources-pane table', '#res-type-'),
]

CASE_IDS = [case[0] for case in CARDS]


def _first_trigger_cell(page, route, tab, ready, prefix):
    """Open the card (and its tab) and return a visible trigger cell.

    Skips only when the snapshot genuinely has no such row — a dataset gap is
    not a regression. Failure to *render the pane at all* is a different thing
    and fails loudly, so a missing fragment can never masquerade as a pass.

    Asserts the trigger is a ``<td>``: if it is still on the ``<tr>`` the bug
    is present, and saying so here is far clearer than the downstream
    assertion failing for a reason the message does not explain.
    """
    visit(page, route)

    if tab:
        page.click(tab)

    try:
        page.wait_for_selector(ready, state='visible', timeout=15_000)
    except Exception:
        raise AssertionError(
            f'{prefix}: {ready!r} never rendered on {route}. The pane did not '
            f'load, so this case would otherwise skip and report a pass '
            f'without having tested anything.'
        ) from None
    page.wait_for_load_state('networkidle')

    row_level = page.locator(f'tr[data-bs-toggle="collapse"][data-bs-target^="{prefix}"]')
    assert row_level.count() == 0, (
        f'{prefix}: the collapse trigger is still on the <tr>, so its action '
        f'buttons will toggle the row (issue #356). Move it onto the '
        f'non-action <td>s with the collapse_toggle macro.'
    )

    cell = page.locator(
        f'td[data-bs-toggle="collapse"][data-bs-target^="{prefix}"]:visible').first
    try:
        cell.wait_for(state='visible', timeout=10_000)
    except Exception:
        pytest.skip(f'no {prefix} group row rendered for this user/dataset')
    return cell


@pytest.mark.parametrize('label,route,tab,ready,prefix', CARDS, ids=CASE_IDS)
def test_action_button_does_not_toggle_the_row(page, label, route, tab, ready, prefix):
    """The #356 assertion: modal opens, row stays put."""
    cell = _first_trigger_cell(page, route, tab, ready, prefix)
    target = cell.get_attribute('data-bs-target')

    assert page.locator(f'{target}.show').count() == 0, (
        f'{label}: {target} is already expanded before the test acts')

    row = cell.locator('xpath=ancestor::tr[1]')
    pencil = row.locator('button[data-bs-toggle="modal"]:visible').first
    if pencil.count() == 0:
        pytest.skip(f'{label}: no edit button on this row for this user')

    pencil.click()
    page.wait_for_selector('.modal.show', timeout=10_000)

    assert page.locator(f'{target}.show').count() == 0, (
        f'{label}: clicking the edit button also expanded {target}. The '
        f'collapse trigger is reaching the button — see issue #356 and '
        f'templates/dashboards/fragments/collapse.html.'
    )


@pytest.mark.parametrize('label,route,tab,ready,prefix', CARDS, ids=CASE_IDS)
def test_the_cell_still_expands_the_row(page, label, route, tab, ready, prefix):
    """The over-correction guard: the group row must remain expandable."""
    cell = _first_trigger_cell(page, route, tab, ready, prefix)
    target = cell.get_attribute('data-bs-target')

    cell.click()
    page.locator(f'{target}.show').wait_for(state='visible', timeout=10_000)
