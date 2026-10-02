"""Admin CRUD tables stay dense, and a save keeps the sub-tab you were on.

Row height is a browser fact: the markup gate
(``tests/unit/webapp/test_admin_table_conventions.py``) cannot see that a 54x38 px
outlined button set every admin row to ~56 px, or that two buttons wrapped.
Measured at the session's 1440x1000 viewport. Design record:
docs/plans/ADMIN_TABLE_POLISH.md.
"""
import pytest

from conftest import visit

#: Desktop budget for a record row with a .row-actions strip (45-47 px today).
ROW_BUDGET_PX = 48

#: (label, route, tab button or None, ready selector, a group trigger to open)
CARDS = [
    ('resources', '/admin/resources', None, '#resources-pane table', '#res-type-'),
    ('machines', '/admin/resources', '#machines-tab', '#machines-pane table', None),
    ('queues', '/admin/resources', '#queues-tab', '#queues-pane table', '#queue-res-'),
    ('organizations', '/admin/organizations', None, '#orgs-tree-table', None),
    ('institutions', '/admin/organizations', '#institutions-tab', '#institutions-table', '#inst-type-'),
    ('areas', '/admin/organizations', '#areas-tab', '#areas-pane table', '#aoi-group-'),
    ('nsf-programs', '/admin/organizations', '#nsf-programs-tab', '#nsf-programs-pane table', None),
    ('contracts', '/admin/contracts', None, '#contractsTable table', '#contract-source-'),
    ('facilities', '/admin/facilities', None, '#facilities-pane table', '#facility-panels-'),
    ('mnemonics', '/admin/organizations/mnemonics', None, '#mnemonicCodesSection table', None),
]

_MEASURE = """(ready) => {
    const scope = document.querySelector(ready).closest('.tab-pane, #contractsTable, #facilities-pane')
        || document.querySelector(ready);
    const rows = [...scope.querySelectorAll('tr')].filter(r =>
        r.offsetParent && r.querySelector(':scope > td > .row-actions'));
    return rows.slice(0, 25).map(r => {
        const strip = r.querySelector('.row-actions');
        const btn = strip.querySelector('.btn');
        return {
            text: r.innerText.replace(/\\s+/g, ' ').trim().slice(0, 50),
            row: r.getBoundingClientRect().height,
            strip: strip.getBoundingClientRect().height,
            btn: btn ? btn.getBoundingClientRect().height : 0,
        };
    });
}"""


def _open(page, route, tab, ready, group):
    visit(page, route)
    if tab:
        page.click(tab)
    try:
        page.wait_for_selector(ready, state='visible', timeout=15_000)
    except Exception:
        raise AssertionError(f'{ready!r} never rendered on {route}') from None
    page.wait_for_load_state('networkidle')
    if group:
        trigger = page.locator(f'[data-bs-toggle="collapse"][data-bs-target^="{group}"]:visible').first
        if trigger.count():
            trigger.click()
            page.locator(f'{trigger.get_attribute("data-bs-target")}.show').wait_for(timeout=10_000)


@pytest.mark.parametrize('label,route,tab,ready,group', CARDS, ids=[c[0] for c in CARDS])
def test_rows_fit_the_budget_and_actions_do_not_wrap(page, label, route, tab, ready, group):
    _open(page, route, tab, ready, group)
    rows = page.evaluate(_MEASURE, ready)
    if not rows:
        pytest.skip(f'{label}: no rows with actions for this user/dataset')

    tall = [r for r in rows if r['row'] > ROW_BUDGET_PX]
    assert not tall, (
        f'{label}: rows over {ROW_BUDGET_PX}px: '
        + ', '.join(f"{r['text']!r}={r['row']:.0f}px" for r in tall[:5]))

    wrapped = [r for r in rows if r['btn'] and r['strip'] > r['btn'] * 1.5]
    assert not wrapped, (
        f'{label}: action strip wrapped onto a second line: '
        + ', '.join(f"{r['text']!r} strip={r['strip']:.0f}px" for r in wrapped[:5]))


def test_saving_from_a_sub_tab_keeps_that_tab(page):
    """_reloadAdminCard used to drop ?tab=, so a save reloaded into the first tab."""
    _open(page, '/admin/resources', '#machines-tab', '#machines-pane table', None)
    pencil = page.locator('#machines-pane button[title="Edit machine"]').first
    if pencil.count() == 0:
        pytest.skip('no editable machine for this user/dataset')
    page.evaluate("document.querySelector('#machines-pane').dataset.e2eMarker = 'before'")

    pencil.click()
    page.wait_for_selector('#editMachineModal.show button[type=submit]', timeout=10_000)
    with page.expect_response(lambda r: '/admin/htmx/resources?' in r.url) as reload:
        page.click('#editMachineModal.show button[type=submit]')
    assert 'tab=machines' in reload.value.url, reload.value.url

    page.wait_for_function(
        "() => document.querySelector('#machines-pane')?.dataset.e2eMarker !== 'before'",
        timeout=10_000)
    assert page.locator('#resourcesTabs .nav-link.active').get_attribute('data-tab-param-value') == 'machines'


def test_fair_share_chart_drills_to_its_facility_row(page):
    """Every sunburst wedge and legend entry is a RowDrill to its facility's tree row."""
    _open(page, '/admin/facilities', None, '.fair-share-chart svg', None)
    links = page.locator('.fair-share-chart svg a')
    # matplotlib writes xlink:href, which a CSS [href] selector does not match.
    hrefs = links.evaluate_all("els => els.map(e => e.getAttribute('xlink:href') || e.getAttribute('href') || '')")
    drills = [i for i, h in enumerate(hrefs) if h.startswith('#sam/row/data-facility-id/')]
    if not drills:
        pytest.skip('no facility with a fair share in this dataset')
    link = links.nth(drills[-1])   # the last legend label
    fid = hrefs[drills[-1]].rsplit('/', 1)[1]
    assert page.locator(f'#facility-panels-{fid}.show').count() == 0
    link.click()
    page.locator(f'#facility-panels-{fid}.show').wait_for(timeout=10_000)
    assert page.locator(f'[data-bs-target="#facility-panels-{fid}"][aria-expanded="true"]').count() > 0
