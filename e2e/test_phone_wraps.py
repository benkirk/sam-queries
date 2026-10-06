"""No chevron wraps away from its text on a small phone (360px), every collapse and tab open.

A wrap is a browser fact: markup cannot show that a squeezed label column put a drill chevron
on the line above its label. The detector is scripts/ui_snapshots.py's ``check_wraps``, so this
gate and the full census (``--wraps --pages wraps``) measure the same thing. CI's snapshot has
no job history or disk scans, so the pages here are the ones it fills. Record:
docs/plans/implemented/PHONE_WRAP_SWEEP_HANDOFF.md.
"""
import importlib.util

import pytest

from conftest import REPO_ROOT, visit

_spec = importlib.util.spec_from_file_location('ui_snapshots', REPO_ROOT / 'scripts' / 'ui_snapshots.py')
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)

PHONE = {'width': 360, 'height': 800}

PAGES = [
    '/user/accounts',                     # project card headers, members and hierarchy toggles
    '/admin/organizations?tab=areas',
    '/admin/resources',
    '/admin/facilities',
    '/admin/contracts',
    '/allocations/projects',
]


def _assert_no_wraps(page, url):
    hits = snap.check_wraps(page)
    assert not hits, f'{url} at {PHONE["width"]}px:\n' + '\n'.join(hits)


@pytest.mark.parametrize('url', PAGES)
def test_no_chevron_wraps_at_phone_width(page, url):
    page.set_viewport_size(PHONE)
    visit(page, url)
    _assert_no_wraps(page, url)


# A label column squeezed to 40px under the real CSS: drill_toggle's markup and a bare chevron.
_DRILL_FIXTURE = """(force) => {
  const t = document.createElement('table');
  t.className = 'table'; t.id = 'wrap-fixture'; t.style.width = '9rem';
  t.innerHTML = `<tbody><tr><td><button type="button" class="btn btn-sm btn-link p-0 me-1" data-bs-toggle="collapse"
      data-bs-target="#x"><i class="fa-solid fa-chevron-right collapse-icon small"></i></button><code>CESM0023</code></td>
      <td class="col-num">1,234,567.89</td></tr>
    <tr><td><i class="fa-solid fa-chevron-right collapse-icon small me-1"></i>
      <code>2026-07-09</code></td>
      <td class="col-num">1,234,567.89</td></tr></tbody>`;
  if (force) t.querySelectorAll('td').forEach(td => td.style.whiteSpace = 'normal');
  document.querySelector('main, body').prepend(t);
}"""


@pytest.mark.parametrize('force_wrap', [False, True], ids=['house-css', 'control'])
def test_a_squeezed_drill_cell_keeps_its_chevron_on_the_label_line(page, force_wrap):
    visit(page, '/user/accounts')
    page.evaluate(_DRILL_FIXTURE, force_wrap)
    hits = [h for h in page.evaluate(snap.WRAP_CHECK_JS) if 'wrap-fixture' in h]
    if force_wrap:   # the detector must see the wrap the house rule prevents
        assert len(hits) == 2, hits
    else:
        assert not hits, hits


def test_manage_project_headers_do_not_wrap_at_phone_width(page):
    page.set_viewport_size(PHONE)
    visit(page, '/user/accounts')
    card = page.locator('.project-card[data-projcode]').first
    if card.count() == 0:
        pytest.skip('no project cards rendered for this user')
    url = f"/admin/project/{card.get_attribute('data-projcode')}/edit"
    visit(page, url)
    _assert_no_wraps(page, url)
