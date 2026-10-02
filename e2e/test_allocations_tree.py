"""Allocations dashboard: a sunburst click opens its facility in its own Resource
tab, expansion follows you across tabs, and the tree stays dense.

Each test starts from cleared localStorage, since collapse and pill state persist.
Design record: docs/plans/ALLOCATIONS_SUNBURST.md.
"""
import pytest

from conftest import visit

ROUTE = '/allocations/projects'
ROW_BUDGET_PX = 48

_PANES = """() => [...document.querySelectorAll('#resourceTabsContent > .tab-pane')].map(p => ({
    id: p.id,
    facilities: [...p.querySelectorAll('tbody[data-alloc-facility]')].map(b => b.dataset.allocFacility),
}))"""


def _fresh(page):
    visit(page, ROUTE)
    page.evaluate('() => localStorage.clear()')
    visit(page, ROUTE)
    return page.evaluate(_PANES)


def _show_tab(page, pane_id):
    page.click(f'#resourceTabs a[href="#{pane_id}"]')
    page.locator(f'#{pane_id}.active.show').wait_for(timeout=10_000)


def _toggle(page, pane_id, key):
    """Click a facility row, as a person would; returns its tbody locator."""
    body = page.locator(f'#{pane_id} tbody[data-alloc-facility="{key}"]')
    page.click(f'#{pane_id} tr[data-bs-target="#{body.get_attribute("id")}"]')
    return body


def _is_open(page, pane_id, key):
    return page.locator(f'#{pane_id} tbody[data-alloc-facility="{key}"].show').count() == 1


def test_sunburst_click_opens_its_facility_in_this_tab_only(page):
    panes = _fresh(page)
    pane = next((p for p in panes if p['facilities']), None)
    if pane is None:
        pytest.skip('no allocations in this dataset')
    _show_tab(page, pane['id'])
    links = page.locator(f'#{pane["id"]} .sunburst-chart svg a')
    # matplotlib writes xlink:href, which a CSS [href] selector does not match.
    hrefs = links.evaluate_all("els => els.map(e => e.getAttribute('xlink:href') || e.getAttribute('href') || '')")
    drills = [i for i, h in enumerate(hrefs) if h.startswith('#sam/row/data-facility-id/')]
    assert drills, 'the sunburst carries no facility drills'
    fid = hrefs[drills[-1]].rsplit('/', 1)[1]
    links.nth(drills[-1]).click()
    page.locator(f'#{pane["id"]} tbody[data-alloc-facility="{fid}"].show').wait_for(timeout=10_000)
    others = [p['id'] for p in panes if p['id'] != pane['id'] and _is_open(page, p['id'], fid)]
    assert not others, f'the click also opened {fid} in {others}'


def test_expansion_and_view_follow_the_resource_tab(page):
    panes = _fresh(page)
    pair = next(((a, b, k) for a in panes for b in panes if a is not b
                 for k in a['facilities'] if k in b['facilities']), None)
    if pair is None:
        pytest.skip('no facility appears under two resources')
    a, b, key = pair
    _show_tab(page, a['id'])
    _toggle(page, a['id'], key)
    page.locator(f'#{a["id"]} tbody[data-alloc-facility="{key}"].show').wait_for(timeout=10_000)
    page.click(f'#{a["id"]} .alloc-view-pills [data-alloc-view="pace"]')
    assert not _is_open(page, b['id'], key)

    _show_tab(page, b['id'])
    page.locator(f'#{b["id"]} tbody[data-alloc-facility="{key}"].show').wait_for(timeout=10_000)
    assert page.locator(f'#{b["id"]} .alloc-view-pills .nav-link.active').get_attribute('data-alloc-view') == 'pace'


def test_tree_rows_stay_dense(page):
    panes = _fresh(page)
    pane = next((p for p in panes if p['facilities']), None)
    if pane is None:
        pytest.skip('no allocations in this dataset')
    _show_tab(page, pane['id'])
    _toggle(page, pane['id'], pane['facilities'][0])
    page.locator(f'#{pane["id"]} tbody.show[data-alloc-facility]').first.wait_for(timeout=10_000)
    heights = page.evaluate(f"""() => [...document.querySelectorAll('#{pane["id"]} table.alloc-tree tr')]
        .filter(r => r.offsetParent && !r.classList.contains('collapse'))
        .map(r => [r.innerText.replace(/\\s+/g, ' ').trim().slice(0, 40), r.getBoundingClientRect().height])""")
    tall = [h for h in heights if h[1] > ROW_BUDGET_PX]
    assert not tall, f'rows over {ROW_BUDGET_PX}px: {tall[:5]}'
