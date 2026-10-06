"""No chevron wraps away from its text, and no word splits across two lines, on a small phone
(360px), every collapse and tab open.

Both are browser facts: markup cannot show that a squeezed label column put a drill chevron on
the line above its label, or that a card column was narrower than its longest word. The
detectors are scripts/ui_snapshots.py's ``check_wraps`` and ``check_midword``, so this gate and
the full census (``--wraps`` / ``--midword --pages wraps``) measure the same thing. CI's snapshot
has no job history or disk scans, so the pages here are the ones it fills. Records:
docs/plans/implemented/PHONE_WRAP_SWEEP_HANDOFF.md, PHONE_MIDWORD_SWEEP_HANDOFF.md.
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


def _open_expirations(page):   # the admin Expirations card renders when its section opens
    toggle = page.locator('[data-bs-target="#expirations-section"]').first
    if toggle.count() and toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()
        page.wait_for_selector('#expirations-section.show', timeout=15_000)
        page.wait_for_load_state('networkidle', timeout=15_000)


@pytest.mark.parametrize('url', [*PAGES, '/admin/projects'])
def test_no_midword_breaks_at_phone_width(page, url):
    page.set_viewport_size(PHONE)
    visit(page, url)
    if url == '/admin/projects':   # the nested card: a card in a card, 208px of content
        _open_expirations(page)
    hits = snap.check_midword(page)
    assert not hits, f'{url} at {PHONE["width"]}px:\n' + '\n'.join(hits)


# One long word in a card (word-wrap: break-word): 60px splits it, 20rem does not.
_MIDWORD_FIXTURE = """(width) => {
  const d = document.createElement('div');
  d.className = 'card'; d.id = 'midword-fixture'; d.style.width = width;
  d.innerHTML = '<div class="card-body"><p class="mb-0">Muknahallipatna</p></div>';
  document.querySelector('main, body').prepend(d);
}"""


@pytest.mark.parametrize('width', ['20rem', '60px'], ids=['roomy', 'control'])
def test_a_squeezed_card_splits_its_word_and_the_detector_sees_it(page, width):
    visit(page, '/user/accounts')
    page.evaluate(_MIDWORD_FIXTURE, width)
    hits = [h for h in page.evaluate(snap.MIDWORD_JS) if 'midword-fixture' in h]
    assert len(hits) == (1 if width == '60px' else 0), hits


def test_manage_project_headers_do_not_wrap_at_phone_width(page):
    page.set_viewport_size(PHONE)
    visit(page, '/user/accounts')
    card = page.locator('.project-card[data-projcode]').first
    if card.count() == 0:
        pytest.skip('no project cards rendered for this user')
    url = f"/admin/project/{card.get_attribute('data-projcode')}/edit"
    visit(page, url)
    _assert_no_wraps(page, url)
