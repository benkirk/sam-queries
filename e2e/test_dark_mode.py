"""Dark mode: does the text actually read?

The narrow question this file answers is the one that matters and that no
Python-tier test can reach — **contrast**. Whether `--text-primary` resolves
to `#e9ecef` is a unit test; whether `#e9ecef` on whatever surface actually
ended up behind it clears a legibility threshold is a browser question,
because the surface is the product of cascade, specificity and Bootstrap's own
dark block all resolving together.

Deliberately NOT pixel snapshots. A 257-template app would produce a snapshot
suite that is noisy, high-maintenance, and fails on every intentional tweak —
pinning appearance rather than the property we care about. Computed contrast
fails only when text becomes hard to read, which is the actual defect.

Deliberately a *handful of representative surfaces*, not a crawl: card body,
page heading, table header, navbar, badge, form control. Each stands for a
whole family, and each was a real risk during the migration — the navbar
because it is pinned light while everything around it went dark, the table
header because `.table-light` had to be replaced wholesale, the badge because
Bootstrap's `-subtle` palette is the one part of this that themes itself.
"""
import pytest

from conftest import THEMES, assert_theme_applied, set_theme, visit

#: WCAG 2.1 AA: 4.5:1 for body text, 3:1 for large text (>=18.66px bold or
#: >=24px). We assert the *large* threshold globally rather than per-element
#: font-size bookkeeping — this is a smoke test for "unreadable", not an
#: accessibility audit, and holding every sampled surface to 3:1 catches the
#: failure mode (dark-on-dark, light-on-light) without producing arguments
#: about a 4.4:1 caption.
MIN_CONTRAST = 3.0

#: Surfaces worth sampling, as (label, CSS selector). A selector that matches
#: nothing is skipped rather than failed — the obfuscated dataset does not
#: guarantee every widget on every page.
SAMPLES = [
    ('page heading',    'h1'),
    ('breadcrumb link', '.breadcrumb a'),
    ('card body text',  '.card .card-body'),
    ('table header',    '.table-subtle th, thead th'),
    ('table cell',      'tbody td'),
    ('navbar link',     '.navbar .nav-link'),
    ('form control',    'input[type="text"], input[type="date"], select'),
    ('muted text',      '.text-muted'),
]

#: Pages chosen for coverage of the sampled families, not for breadth.
PAGES = ['/user/info', '/allocations/transactions', '/status/derecho']


# --------------------------------------------------------------------------
# WCAG relative luminance / contrast ratio
# --------------------------------------------------------------------------

def _channel(value):
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG 2.1 contrast ratio between two opaque (r, g, b) triples."""
    a, b = _luminance(fg), _luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


#: Walks up the ancestor chain for the first non-transparent background, which
#: is what the eye actually sees behind the text — an element with
#: `background: transparent` inside a card is read against the *card*.
#: Composites any partially transparent layers it finds on the way, so a
#: `bg-opacity-25` wash is measured as rendered rather than as declared.
_EFFECTIVE_COLOURS_JS = """
(selector) => {
  const parse = (s) => {
    const m = (s || '').match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x.trim()));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });

  const el = document.querySelector(selector);
  if (!el) return null;
  // Skip anything the user cannot see; an invisible element has no contrast
  // obligation and would otherwise produce noise.
  const box = el.getBoundingClientRect();
  const vis = getComputedStyle(el);
  if (!box.width || !box.height || vis.visibility === 'hidden' ||
      vis.display === 'none' || parseFloat(vis.opacity) === 0) return null;
  if (!(el.textContent || '').trim()) return null;

  const fg = parse(vis.color);
  if (!fg) return null;

  let bg = { r: 255, g: 255, b: 255, a: 1 };   // the canvas, as a last resort
  const stack = [];
  for (let n = el; n; n = n.parentElement) {
    const c = parse(getComputedStyle(n).backgroundColor);
    if (c && c.a > 0) { stack.push(c); if (c.a === 1) break; }
  }
  for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);

  return { fg: [fg.r, fg.g, fg.b], bg: [bg.r, bg.g, bg.b],
           text: (el.textContent || '').trim().slice(0, 40) };
}
"""


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('page_url', PAGES)
def test_sampled_surfaces_are_legible(page, base_url, page_url, theme):
    set_theme(page, base_url, theme)
    visit(page, page_url)
    assert_theme_applied(page, theme)

    failures, checked = [], 0
    for label, selector in SAMPLES:
        found = page.evaluate(_EFFECTIVE_COLOURS_JS, selector)
        if not found:
            continue                      # not on this page, or not visible
        checked += 1
        ratio = contrast_ratio(found['fg'], found['bg'])
        if ratio < MIN_CONTRAST:
            failures.append(
                f'  {label} ({selector}): {ratio:.2f}:1 '
                f'fg=rgb{tuple(round(v) for v in found["fg"])} '
                f'bg=rgb{tuple(round(v) for v in found["bg"])} '
                f'text={found["text"]!r}')

    assert checked, (
        f'{page_url} ({theme}): none of the sampled selectors matched a '
        f'visible element — the sample list is stale, and this test is '
        f'silently passing without checking anything')
    assert not failures, (
        f'{page_url} ({theme}) has text below {MIN_CONTRAST}:1:\n'
        + '\n'.join(failures))


def test_clicking_the_toggle_persists_the_choice(page, base_url):
    """The real control, end to end: click -> cookie -> reload -> next page.

    This is the one test that must drive the toggle rather than set the cookie,
    because the property under test *is* the cookie the toggle writes. An
    earlier draft asserted persistence on a cookie the test itself had added
    via `add_cookies` — which creates a SESSION cookie, so it failed against
    perfectly correct application code. Asserting a property of your own
    fixture proves nothing; drive the thing that ships.

    What it guards: `sam_layout` is deliberately a session cookie (a viewport
    is a property of the visit), and copying that here would silently reset
    every user to light on every browser restart.
    """
    set_theme(page, base_url, 'light')
    visit(page, '/user/info')
    assert_theme_applied(page, 'light')

    page.click('.utility-menu [data-theme-toggle]')
    page.wait_for_load_state('domcontentloaded')
    assert_theme_applied(page, 'dark')

    cookie = next((c for c in page.context.cookies()
                   if c['name'] == 'sam_theme'), None)
    assert cookie is not None, 'the toggle did not write a sam_theme cookie'
    assert cookie['value'] == 'dark'
    assert cookie.get('expires', -1) > 0, (
        'the toggle wrote a SESSION cookie — a theme is a preference and must '
        'persist, or every browser restart resets the user to light mode')

    # And the choice survives navigation to an unrelated page.
    visit(page, '/allocations/transactions')
    assert_theme_applied(page, 'dark')


@pytest.mark.parametrize('theme', THEMES)
def test_login_page_carries_the_theme(page, base_url, theme):
    """A separate `<html>` shell with its own CSS and its own <script> tag —
    the one most likely to be missed, and the only page an anonymous visitor
    ever sees."""
    page.context.clear_cookies()
    set_theme(page, base_url, theme)
    response = page.goto('/auth/login')
    assert response is not None and response.status == 200
    assert_theme_applied(page, theme)

    toggles = page.locator('[data-theme-toggle]')
    assert toggles.count() >= 1, (
        'the login page has no theme toggle; an anonymous visitor would have '
        'no way to change it')
