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

#: Pages that draw a chart — the two families that cover the interesting cases:
#: pies (text sits *on* a wedge) and stacked areas (text sits on the card). The
#: jobs and disk-scans surfaces draw charts too but are plugin-gated, and CI
#: runs without those plugins.
CHART_PAGES = ['/allocations/projects', '/status/derecho']

#: Chart pages whose data comes from the collector-fed `system_status` tables
#: rather than the SAM snapshot, and which therefore draw nothing on a stack
#: that has never had a collector pointed at it.
#:
#: `/status/derecho`'s only chart is the user/project load area chart, off
#: `user_proj_queue_status` — and the obfuscated dump CI restores carries zero
#: rows of it (the `*_status` rows it does carry are as old as the snapshot,
#: while the chart windows the last 168 hours). So the card renders
#: `UserProjAreaChart.empty_message` and there is no SVG to measure. Locally,
#: where collectors have been running, the same page draws ~60k rows' worth
#: and is measured for real.
#:
#: Skipped when the page drew **nothing**; never when it drew something. A
#: figure with no `<text>` in it still fails here, which is the property that
#: keeps this test honest — see below.
COLLECTOR_FED_CHART_PAGES = {'/status/derecho'}


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


#: Every ``<text>`` in every chart on the page, each measured against whatever
#: is *actually* behind that glyph.
#:
#: The ancestor-background walk the chrome sampler uses is wrong here, and
#: wrong in the direction that hides the defect: a pie's percentage label sits
#: on a **wedge**, which is a sibling ``<path>``, not an ancestor. Walking
#: parents would measure white-on-card for a label that is really white-on-gold
#: and report a failure that is not real — or, worse, pass a label that is
#: genuinely unreadable on its wedge.
#:
#: So this hit-tests the point instead: ``elementsFromPoint`` returns the paint
#: order front-to-back, and the first thing under the glyph is the truth.
#: Shape tags are checked explicitly because ``<g>`` and ``<svg>`` inherit a
#: ``fill`` and would otherwise answer for a backdrop they do not paint.
_CHART_TEXT_COLOURS_JS = """
() => {
  const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'polygon']);
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

  const out = [];
  // Every matplotlib figure on the page, whether or not it carries <text>.
  // `g#figure_1` is matplotlib's own wrapper and is emitted at both
  // `svg.fonttype` settings, so counting figures separates "this page drew
  // no chart at all" (a data question) from "this page drew charts whose
  // glyphs are paths" (the regression this test exists to catch).
  const figures = [...document.querySelectorAll('svg')]
      .filter(s => s.querySelector('g[id^="figure_"]'));
  const charts = figures.filter(s => s.querySelector('text'));

  for (const svg of charts) {
    // elementsFromPoint is viewport-relative, so the chart has to be on
    // screen before any of its glyphs can be hit-tested.
    svg.scrollIntoView({ behavior: 'instant', block: 'center' });

    for (const el of svg.querySelectorAll('text')) {
      const label = (el.textContent || '').trim();
      if (!label) continue;
      const box = el.getBoundingClientRect();
      if (!box.width || !box.height) continue;
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) continue;

      const fg = parse(getComputedStyle(el).fill);
      if (!fg) continue;

      const stack = [];
      for (const node of document.elementsFromPoint(cx, cy)) {
        if (node === el || el.contains(node)) continue;
        const style = getComputedStyle(node);
        const tag = node.tagName.toLowerCase();
        const paint = SHAPES.has(tag) ? parse(style.fill)
                                      : parse(style.backgroundColor);
        if (!paint || !paint.a) continue;
        stack.push(paint);
        if (paint.a === 1) break;
      }

      let bg = { r: 255, g: 255, b: 255, a: 1 };   // the canvas, last resort
      for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
      out.push({ fg: [fg.r, fg.g, fg.b], bg: [bg.r, bg.g, bg.b], text: label });
    }
  }
  return { figures: figures.length, samples: out };
}
"""


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('page_url', CHART_PAGES)
def test_chart_text_is_legible(page, base_url, page_url, theme):
    """Chart ink, which is the one surface CSS cannot reach.

    Charts are matplotlib SVGs with colours baked in at render time, so no
    stylesheet can retheme them — the server has to know the theme, which is
    the whole reason `sam_theme` is a cookie rather than `localStorage`. Before
    the chart layer applied `Theme`, every label on every chart carried
    `fill="rgb(1,24,55)"` against the `#1b2733` card: **1.3:1**, on pages that
    had already been eyeballed and called fine.

    That is the third time in this migration the same defect — brand colour
    used as foreground — was found by measurement after screenshots missed it
    (see DARK_MODE.md Appendix E), which is why it is asserted rather than
    reviewed.
    """
    set_theme(page, base_url, theme)
    visit(page, page_url)
    assert_theme_applied(page, theme)

    found_charts = page.evaluate(_CHART_TEXT_COLOURS_JS)
    figures, samples = found_charts['figures'], found_charts['samples']

    if not figures and page_url in COLLECTOR_FED_CHART_PAGES:
        pytest.skip(
            f'{page_url} drew no chart — its data comes from the collector-fed '
            f'system_status tables, which the obfuscated fixture does not '
            f'carry. See COLLECTOR_FED_CHART_PAGES.')

    assert figures, (
        f'{page_url} ({theme}) drew no chart at all — the page stopped '
        f'rendering charts, or its data went away.')
    assert samples, (
        f'{page_url} ({theme}) drew {figures} chart(s) but not one glyph of '
        f'text: `svg.fonttype` left "none" and every glyph is a path again — '
        f'in which case this test is silently vacuous.')

    failures = []
    for found in samples:
        ratio = contrast_ratio(found['fg'], found['bg'])
        if ratio < MIN_CONTRAST:
            failures.append(
                f'  {ratio:.2f}:1 '
                f'fg=rgb{tuple(round(v) for v in found["fg"])} '
                f'bg=rgb{tuple(round(v) for v in found["bg"])} '
                f'text={found["text"]!r}')

    assert not failures, (
        f'{page_url} ({theme}): {len(failures)} of {len(samples)} chart labels '
        f'are below {MIN_CONTRAST}:1:\n' + '\n'.join(failures))


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
