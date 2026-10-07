#!/usr/bin/env python3
"""Screenshot pages in every layout x theme into a folder, for by-eye before/after review.

No baselines, no CI: run it on the base branch and on yours, then compare the two
folders (the middle ground in docs/plans/GALLERY_VISUAL_SNAPSHOTS.md). Needs the
[e2e] extra. Logs in through the stub form unless given --storage-state.
--styles also dumps every element's computed style; --compare proves "no visual change" for a
change that keeps the DOM. A restructure moves every element path, so prove that one with
--element SELECTOR on both sides and --compare-pixels.
--headers lists table headers whose sort icon wrapped onto a line of its own; --wraps lists chevrons
wrapped away from their text and --midword words split across two lines (a column too narrow for
its content), both with every collapse and tab open (none of the three needs --out).
--modal OPENER (after any --step) or --recipes FILE shoots the opened dialog and prints its height.
--pages charts shoots every chart host, each with the steps its lazy tabs need.
A recipe without "modal" is a page shot (the deck's: ui_snapshots_deck.json). Against production:
--read-only aborts every request but GET/HEAD, --redact swaps names and emails for pseudonyms in the
page before capture, --verify OCRs each PNG and deletes it on a leak (SAMUEL_PROD_SCREENSHOTS_HANDOFF.md).

    python scripts/ui_snapshots.py --out /tmp/before
    python scripts/ui_snapshots.py --out /tmp/after --page /admin/contracts --expand 2
    python scripts/ui_snapshots.py --styles --out /tmp/after && python scripts/ui_snapshots.py --compare /tmp/before /tmp/after
    python scripts/ui_snapshots.py --out /tmp/after --page /allocations/projects --element .filter-sidebar
    python scripts/ui_snapshots.py --compare-pixels /tmp/before /tmp/after
    python scripts/ui_snapshots.py --headers --layout desktop --layout mobile --theme light
    python scripts/ui_snapshots.py --wraps --pages wraps --layout mobile --width 360 --theme light
    python scripts/ui_snapshots.py --midword --pages wraps --layout mobile --width 360 --theme light
    python scripts/ui_snapshots.py --out /tmp/m --page /admin/resources --modal '[data-bs-target="#createResourceModal"]'
    python scripts/ui_snapshots.py --out /tmp/m --recipes scripts/ui_snapshots_modals.json --layout desktop --layout mobile
    python scripts/ui_snapshots.py --out /tmp/c --pages charts --base-url http://localhost:5050
    python scripts/ui_snapshots.py --out /tmp/deck --recipes scripts/ui_snapshots_deck.json --base-url http://localhost:5052
    python scripts/ui_snapshots.py --out /tmp/f --recipes scripts/ui_snapshots_prod_deck.json --base-url https://sam.hpc.ucar.edu \
        --storage-state /tmp/prod_state.json --read-only --redact --verify
"""
import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_PAGES = [
    '/admin/resources', '/admin/resources?tab=machines', '/admin/resources?tab=queues',
    '/admin/organizations', '/admin/organizations?tab=institutions', '/admin/organizations?tab=areas',
    '/admin/contracts', '/admin/facilities', '/admin/account-requests', '/admin/events',
    '/status/derecho', '/status/casper', '/status/jupyterhub', '/status/events', '/dev/gallery',
]
_JOBS = '/dashboards/user/jobs/machine/derecho/explore'
_DETAILS = '/user/resource-details/SCSG0001?resource='
# Named page sets for --pages: (name, url, steps). A chart behind a lazy tab or below the fold
# needs its tab clicked or its loader scrolled to before the shot.
PAGE_SETS = {'charts': [
    ('alloc-share', '/allocations/projects', []),
    ('alloc-pace', '/allocations/projects', ['click:[aria-controls^="pace-"]:visible',
                                            'wait:.pace-chart:visible svg']),
    ('alloc-calendar', '/allocations/projects?view=calendar', []),
    ('admin-facilities', '/admin/facilities', ['wait:.fair-share-chart svg']),
    ('status-derecho', '/status/derecho', ['scroll:[id^="user-proj-chart"]']),
    ('status-queue', '/status/queue-history/derecho/main', ['scroll:[id^="user-proj-chart"]']),
    ('status-nodetype', '/status/nodetype-history/casper/cpu', []),
    ('status-partition', '/status/partition-history/derecho/cpu', []),
    ('status-jobs-byproj', '/status/job-history', ['click:[data-bs-target$="-byproj"]']),
    ('jobs-timeline', _JOBS, ['wait:.jobs-timeline-chart svg']),
    ('jobs-byuser', _JOBS, ['click:[data-bs-target$="-byuser"]', 'wait:.jobs-user-pie svg']),
    ('jobs-byproj', _JOBS, ['click:[data-bs-target$="-byproj"]', 'wait:.jobs-user-pie svg']),
    ('jobs-wait', _JOBS, ['click:[data-bs-target$="-wait"]', 'wait:.jobs-histogram-chart svg']),
    ('jobs-sizes', _JOBS, ['click:[data-bs-target$="-sizes"]', 'wait:.jobs-histogram-chart svg']),
    ('details-compute', _DETAILS + 'Derecho', ['wait:#tab-usage-history svg']),
    ('details-byuser', _DETAILS + 'Derecho', ['click:[data-bs-target="#tab-usage-byuser"]']),
    ('details-disk', _DETAILS + 'Campaign_Store', []),
    ('user-jobs', '/user/jobs', []),
    ('user-data', '/user/data', []),
]}
LAYOUTS = {'mobile': (390, 844), 'tablet': (1024, 1366), 'desktop': (1440, 1000)}
THEMES = ('light', 'dark')


def _login(browser, base_url, username, password):
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    page.goto('/auth/login')
    page.fill('#username', username)
    page.fill('#password', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state('domcontentloaded')
    if '/auth/login' in page.url:
        raise SystemExit(f'login as {username} failed at {base_url}')
    state = context.storage_state()
    context.close()
    return state


def _slug(url):
    return re.sub(r'[^a-z0-9]+', '-', url.lower()).strip('-') or 'root'


# The phone-wrap census (--wraps --pages wraps): every page with a chevron, tabs included. Pages,
# not fragments: /admin/expirations and /allocations/xras_remediations load bare, with no CSS.
PAGE_SETS['wraps'] = [(_slug(url), url, []) for url in [
    '/admin/', '/admin/account-requests', '/admin/configuration', '/admin/contracts', '/admin/events',
    '/admin/facilities', '/admin/organizations', '/admin/organizations?tab=institutions',
    '/admin/organizations?tab=areas', '/admin/organizations/mnemonics', '/admin/projects',
    '/admin/projects/directories', '/admin/resources', '/admin/resources?tab=machines',
    '/admin/resources?tab=queues', '/admin/roles', '/admin/users-groups', '/admin/users/last-seen',
    '/admin/project/SCSG0001', '/admin/project/SCSG0001/edit', '/admin/project/CESM0002',
    '/allocations/', '/allocations/projects', '/allocations/projects?view=calendar',
    '/allocations/adjustments', '/allocations/transactions', '/allocations/xras', '/database/',
    '/status/derecho', '/status/casper', '/status/jupyterhub', '/status/events',
    '/status/job-history', '/status/filesystem-scans',
    '/user/', '/user/accounts', '/user/data', '/user/events', '/user/info', '/user/jobs',
    _DETAILS + 'Derecho', '/user/resource-details/CESM0002?resource=Derecho',
    '/user/resource-details/CESM0002?resource=Campaign_Store',
    '/user/resource-details/P93300042?resource=Casper',
    '/dashboards/user/jobs/CESM0002/explore?machine=derecho&start=2026-09-01&end=2026-09-08',
    '/dashboards/user/disk-scans/CESM0002/directories/explore?resource=Campaign_Store',
    '/project-invitations/SCSG0001/invitations', '/dev/gallery',
]]


# Every element's computed style (and any ::before/::after with content), path-keyed. Styles are
# interned because most elements share a few dozen distinct ones. SVG internals are chart bytes.
# Animations are frozen at t=0 first, or a spinner's transform differs on every capture.
STYLE_DUMP_JS = """() => {
  document.getAnimations().forEach(a => { a.pause(); a.currentTime = 0; });
  const styles = {}, ids = new Map(), elements = {};
  const intern = cs => {
    const o = {};
    for (let i = 0; i < cs.length; i++) o[cs[i]] = cs.getPropertyValue(cs[i]);
    const key = JSON.stringify(o);
    if (!ids.has(key)) { ids.set(key, ids.size); styles[ids.size - 1] = o; }
    return ids.get(key);
  };
  const path = el => {
    const parts = [];
    for (let e = el; e; e = e.parentElement) {
      let i = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) i++;
      parts.unshift(e.tagName.toLowerCase() + (i > 1 ? `:nth-of-type(${i})` : ''));
    }
    return parts.join('>');
  };
  for (const el of document.querySelectorAll('html, body, body *')) {
    if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
    const p = path(el);
    elements[p] = intern(getComputedStyle(el));
    for (const pseudo of ['::before', '::after']) {
      const cs = getComputedStyle(el, pseudo), content = cs.getPropertyValue('content');
      if (content && content !== 'none' && content !== 'normal') elements[p + pseudo] = intern(cs);
    }
  }
  return {styles, elements};
}"""


_ORIGIN = re.compile(r'https?://[^/"\')\s]+')   # a resolved url() names its server; before and after differ


def diff_styles(before, after):
    """``(element, property, before, after)`` for every difference between two style dumps."""
    def resolved(dump):
        styles = {sid: {k: _ORIGIN.sub('', v) for k, v in style.items()} for sid, style in dump['styles'].items()}
        return {el: styles[str(sid)] for el, sid in dump['elements'].items()}
    a, b = resolved(before), resolved(after)
    out = [(el, '(element)', 'present', 'missing') for el in sorted(a.keys() - b.keys())]
    out += [(el, '(element)', 'missing', 'present') for el in sorted(b.keys() - a.keys())]
    for el in sorted(a.keys() & b.keys()):
        out += [(el, prop, a[el].get(prop), b[el].get(prop))
                for prop in sorted(a[el].keys() | b[el].keys()) if a[el].get(prop) != b[el].get(prop)]
    return out


_PX = re.compile(r'-?\d+(?:\.\d+)?(?=px)')


def _within(a, b, tolerance):
    """Two computed values that differ only in px lengths, each by at most ``tolerance``."""
    if not tolerance or a is None or b is None or _PX.sub('#', a) != _PX.sub('#', b):
        return False
    return all(abs(float(x) - float(y)) <= tolerance for x, y in zip(_PX.findall(a), _PX.findall(b)))


def compare_dirs(before_dir, after_dir, top=20, strict=False, px_tolerance=0.0):
    """Print the differences between two --styles folders; 1 when any element differs.

    A custom property (``--bs-btn-hover-bg``) is an input, not paint: where it reaches the page at
    rest a standard property differs too. So one that differs alone is counted but does not fail,
    unless ``strict``. What that can hide is a state the capture never enters (hover, focus).
    """
    names = sorted({p.name for p in Path(before_dir).glob('*.styles.json.gz')}
                   | {p.name for p in Path(after_dir).glob('*.styles.json.gz')})
    if not names:
        print(f'no *.styles.json.gz in {before_dir} or {after_dir}')
        return 1
    failed = 0
    for name in names:
        pa, pb = Path(before_dir) / name, Path(after_dir) / name
        if not (pa.exists() and pb.exists()):
            print(f'{name}: only in {before_dir if pa.exists() else after_dir}')
            failed += 1
            continue
        diffs = [d for d in diff_styles(*(json.loads(gzip.decompress(f.read_bytes())) for f in (pa, pb)))
                 if not _within(d[2], d[3], px_tolerance)]
        rendered = diffs if strict else [d for d in diffs if not d[1].startswith('--')]
        only_custom = {d[0] for d in diffs} - {d[0] for d in rendered}
        note = f' ({len(only_custom)} more differ only in custom properties)' if only_custom else ''
        print(f'{name}: {len({d[0] for d in rendered})} elements differ{note}' if rendered
              else f'{name}: same{note}')
        for el, prop, va, vb in rendered[:top]:
            print(f'    {el}  {prop}: {va} -> {vb}')
        failed += bool(rendered)
    print(f'{failed} of {len(names)} captures differ')
    return 1 if failed else 0


def compare_pixels(before_dir, after_dir):
    """Print where same-named PNGs in two folders differ; 1 when any does (needs Pillow)."""
    from PIL import Image, ImageChops
    names = sorted({p.name for p in Path(before_dir).glob('*.png')} | {p.name for p in Path(after_dir).glob('*.png')})
    if not names:
        print(f'no *.png in {before_dir} or {after_dir}')
        return 1
    failed = 0
    for name in names:
        pa, pb = Path(before_dir) / name, Path(after_dir) / name
        if not (pa.exists() and pb.exists()):
            print(f'{name}: only in {before_dir if pa.exists() else after_dir}')
            failed += 1
            continue
        a, b = Image.open(pa).convert('RGB'), Image.open(pb).convert('RGB')
        box = None if a.size != b.size else ImageChops.difference(a, b).getbbox()
        if a.size != b.size:
            print(f'{name}: size {a.size[0]}x{a.size[1]} -> {b.size[0]}x{b.size[1]}')
        elif box:
            print(f'{name}: differs within {box}')
        else:
            print(f'{name}: identical')
        failed += a.size != b.size or bool(box)
    print(f'{failed} of {len(names)} images differ')
    return 1 if failed else 0


# .col-num / .col-shrink headers wrap at their spaces (components.css), so a sort icon must stay
# joined to its last word (&nbsp;): flag one that sits below every line of its label. Collapsed
# groups are shown first so their columns size as they will in use.
HEADER_CHECK_JS = """() => {
  document.querySelectorAll('tr.collapse, tbody.collapse').forEach(e => e.classList.add('show'));
  const bad = [];
  for (const th of document.querySelectorAll('table th')) {
    if (!th.offsetParent || !/\\bcol-(num|shrink)\\b/.test(th.className)) continue;
    const label = th.textContent.trim().replace(/\\s+/g, ' ');
    const rects = [], tw = document.createTreeWalker(th, NodeFilter.SHOW_TEXT);
    for (let n; (n = tw.nextNode());) {
      if (!n.textContent.trim()) continue;
      const r = document.createRange(); r.selectNodeContents(n); rects.push(...r.getClientRects());
    }
    if (!rects.length) continue;
    const bottom = Math.max(...rects.map(r => r.bottom));
    if ([...th.querySelectorAll('i')].some(i => { const r = i.getBoundingClientRect(); return r.height && r.top >= bottom - 2; }))
      bad.push(`sort icon alone on a line: "${label}"`);
  }
  return bad;
}"""


# Phone-width wraps: a chevron or caret on a line above its text, or a trailing one alone on the
# last line. Rows only get .show: opening them through Bootstrap fires every lazy drill's fetch
# (status Job History's 600 drawers stalled the census). Cards and tabs open for real.
WRAP_FORCE_OPEN_JS = """() => {
  document.querySelectorAll('.collapse:not(.show)').forEach(e => {
    if (e.closest('nav') || e.closest('.modal')) return;
    if (e.matches('tr, tbody')) { e.classList.add('show'); return; }
    try { bootstrap.Collapse.getOrCreateInstance(e, {toggle: false}).show(); } catch (_) { e.classList.add('show'); }
  });
}"""
WRAP_TABS_JS = """() => [...document.querySelectorAll('[data-bs-toggle="tab"], [data-bs-toggle="pill"]')]
  .filter(t => t.offsetParent && !t.closest('.modal')).map(t => t.textContent.trim().slice(0, 30))"""
WRAP_CLICK_TAB_JS = """i => { const t = [...document.querySelectorAll('[data-bs-toggle="tab"], [data-bs-toggle="pill"]')]
  .filter(t => t.offsetParent && !t.closest('.modal'))[i]; if (t) t.click(); }"""
WRAP_CHECK_JS = r"""() => {
  const ICON = 'i.collapse-icon, i.accordion-chevron, i.fa-chevron-right, i.fa-chevron-down, i.fa-caret-right, i.fa-caret-down, i.fa-angle-right, i.fa-angle-left';
  const BOX = 'td, th, .card-header, .accordion-button, li, .list-group-item, h1, h2, h3, h4, h5, h6, label, summary, .btn, a, button';
  const out = [], seen = new Set();
  const rects = (node, from, to) => { const rg = document.createRange(); rg.setStart(node, from); rg.setEnd(node, to); return rg.getClientRects(); };
  for (const icon of document.querySelectorAll(ICON)) {
    if (icon.closest('thead a, .dropdown-menu, nav.navbar')) continue;
    let ib = icon; const btn = icon.closest('button, a');   // an icon-only button wraps as one
    if (btn && !btn.textContent.trim()) ib = btn;
    let box = ib.parentElement && ib.parentElement.closest(BOX);   // the smallest box with other text
    while (box && !box.textContent.replace(ib.textContent, '').trim()) box = box.parentElement && box.parentElement.closest(BOX);
    const r0 = ib.getBoundingClientRect();
    if (!box || !r0.width || !r0.height || seen.has(box)) continue;
    seen.add(box);
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    let node, after = null, before = null, txt = '';
    while ((node = walker.nextNode())) {
      if (ib.contains(node)) continue;
      const t = node.textContent, i = t.search(/\S/);
      if (i < 0) continue;
      const pos = ib.compareDocumentPosition(node);
      if ((pos & Node.DOCUMENT_POSITION_FOLLOWING) && !after) {
        const rs = rects(node, i, i + 1); if (rs.length) { after = rs[0]; txt = t.trim().slice(0, 40); }
      } else if (pos & Node.DOCUMENT_POSITION_PRECEDING) {
        const j = t.trimEnd().length, rs = rects(node, j - 1, j);
        if (rs.length) { before = rs[rs.length - 1]; if (!txt) txt = t.trim().slice(-40); }
      }
    }
    let kind = null;
    if (after && r0.bottom <= after.top + 1) kind = 'icon above its text';
    else if (!after && before && r0.top >= before.bottom - 1) kind = 'trailing icon alone on a line';
    const host = box.closest('[id]');
    if (kind) out.push(`${kind}: "${txt}" in ${box.tagName.toLowerCase()}${host ? ' #' + host.id : ''}`);
  }
  return out;
}"""
# Mid-word: a letter/digit run whose glyph rects sit on two lines. Bootstrap's .card sets
# word-wrap: break-word, so inside a card this marks a column narrower than its longest word.
# One hit per (host id, box, table column, word); the box is the cell, else the nearest block.
MIDWORD_JS = r"""() => {
  const out = [], seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    const el = n.parentElement;
    if (!el || !el.offsetParent || el.closest('svg, script, style, .offcanvas, .modal:not(.show), nav.navbar')) continue;
    const re = /[A-Za-z0-9]{2,}/g; let m;
    while ((m = re.exec(n.textContent))) {
      const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
      if (new Set([...rg.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top))).size < 2) continue;
      const cell = el.closest('td, th');
      let box = cell || el; while (box && getComputedStyle(box).display.startsWith('inline')) box = box.parentElement;
      let col = '';
      if (cell) { const i = [...cell.parentElement.children].indexOf(cell), th = cell.closest('table').querySelector('thead tr');
                  col = ` col ${i}` + (th && th.children[i] ? ` "${th.children[i].textContent.trim().replace(/\s+/g, ' ').slice(0, 24)}"` : ''); }
      const host = box.closest('[id]'), sig = box.tagName.toLowerCase() + [...box.classList].slice(0, 3).map(c => '.' + c).join('');
      const key = (host ? host.id : '') + '|' + sig + '|' + col + '|' + m[0];
      if (seen.has(key)) continue; seen.add(key);
      out.push(`mid-word: "${m[0].slice(0, 30)}" in ${sig} ${Math.round(box.getBoundingClientRect().width)}px${col}${host ? ' #' + host.id : ''}`);
    }
  }
  return out;
}"""


def open_everything(page, sample, max_tabs=14):
    """Force every collapse open and call sample(''); then click each tab, reopen, and call sample(tab)."""
    def force_open(rounds):   # a lazy drill can reveal more collapses
        for _ in range(rounds):
            page.evaluate(WRAP_FORCE_OPEN_JS)
            page.wait_for_timeout(1000)
            _idle(page)

    force_open(3)
    sample('')
    for i, tab in enumerate(page.evaluate(WRAP_TABS_JS)[:max_tabs]):
        page.evaluate(WRAP_CLICK_TAB_JS, i)
        page.wait_for_timeout(800)
        force_open(2)
        sample(tab)


def _collect(page, check_js, max_tabs):
    found = {}

    def sample(tab):
        for line in page.evaluate(check_js):
            found.setdefault(line, f'{line} [tab {tab}]' if tab else line)

    open_everything(page, sample, max_tabs)
    return list(found.values())


def check_wraps(page, max_tabs=14):
    """Open every collapse and tab on the page; return each wrapped chevron, deduplicated."""
    return _collect(page, WRAP_CHECK_JS, max_tabs)


def check_midword(page, max_tabs=14):
    """Open every collapse and tab on the page; return each word split across two lines, deduplicated."""
    return _collect(page, MIDWORD_JS, max_tabs)


_VERBS = ('click', 'wait', 'reveal', 'scroll', 'top', 'remove')


def parse_step(text):
    """``VERB:SEL`` (one of ``_VERBS``) or ``fill:SEL=TEXT`` -> ``(verb, selector, text)``."""
    verb, _, rest = text.partition(':')
    if verb == 'fill':
        selector, sep, value = rest.rpartition('=')
        if not sep or not selector:
            raise ValueError(f'fill step needs SEL=TEXT: {text!r}')
        return verb, selector, value
    if verb not in _VERBS or not rest:
        raise ValueError(f'step must be {", ".join(v + ":SEL" for v in _VERBS)} or fill:SEL=TEXT: {text!r}')
    return verb, rest, None


# Rendered height, and the natural height a fullscreen (phone) dialog would need unclipped.
DIALOG_HEIGHT_JS = """() => {
  const c = document.querySelector('.modal.show .modal-content');
  const parts = c.querySelectorAll('.modal-header, .modal-body, .modal-footer');
  const natural = [...parts].reduce((sum, el) => sum + el.scrollHeight, 0);
  return {height: Math.round(c.getBoundingClientRect().height), natural};
}"""
# Lets the screenshot show a scrolling fullscreen body whole; applied after measuring.
UNCLIP_CSS = ('.modal.show .modal-dialog, .modal.show .modal-content { height: auto !important; }'
              ' .modal.show .modal-body { overflow: visible !important; }')


def _run_steps(page, steps):
    for verb, selector, value in map(parse_step, steps):
        target = page.locator(selector).first
        if verb == 'reveal':   # open collapsed rows without clicking their toggles one by one
            page.evaluate("s => document.querySelectorAll(s).forEach(e => e.classList.add('show'))", selector)
        elif verb == 'scroll':   # every match: an `intersect once` loader fires when it is seen
            page.evaluate("s => document.querySelectorAll(s).forEach("
                          "e => e.offsetParent && e.scrollIntoView({block: 'center'}))", selector)
        elif verb == 'top':   # the first visible match at the top of the window, for a viewport shot
            page.evaluate("s => { const e = [...document.querySelectorAll(s)].find(e => e.offsetParent);"
                          " if (e) window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY - 16); }",
                          selector)
        elif verb == 'remove':   # chrome a shot should not carry (a stale-collector banner)
            page.evaluate("s => document.querySelectorAll(s).forEach(e => e.remove())", selector)
        elif verb == 'fill':
            target.fill(value)
        elif verb == 'click':
            target.click()
        else:
            try:
                target.wait_for(state='visible', timeout=15_000)
            except Exception:  # noqa: BLE001 - a chart with no data here: shoot what loaded
                print(f'  wait:{selector} never visible')
        _idle(page)
        page.wait_for_timeout(300)   # a debounced typeahead or a collapse transition


def _open_modal(page, recipe):
    _run_steps(page, recipe.get('steps', ()))
    page.locator(recipe['modal']).first.click()
    page.wait_for_selector('.modal.show .modal-dialog')
    _idle(page)
    try:   # a lazy body, then any lazy pane inside it (an email preview)
        page.locator('.modal.show .fa-spin >> visible=true').wait_for(state='detached', timeout=15_000)
    except Exception:  # noqa: BLE001 - a spinner that never stops: shoot what loaded
        pass
    _idle(page)
    if recipe.get('inject'):
        html = Path(recipe['inject']['file']).read_text()
        page.evaluate("([sel, html]) => { const el = document.querySelector(sel);"
                      " el.innerHTML = html; window.htmx && htmx.process(el); }",
                      [recipe['inject']['into'], html])
    page.wait_for_timeout(400)   # the fade


def _idle(page):
    try:
        page.wait_for_load_state('networkidle', timeout=15_000)
    except Exception:  # noqa: BLE001 - a long-poll page never idles; shoot what loaded
        pass


def _settle(page, expand):
    _idle(page)
    triggers = page.locator('.tab-pane.active [data-bs-toggle="collapse"]:visible, '
                            '#contractsTable [data-bs-toggle="collapse"]:visible, '
                            '#facilities-pane [data-bs-toggle="collapse"]:visible')
    for i in range(min(expand, triggers.count())):
        triggers.nth(i).click()
        page.wait_for_timeout(400)


def _shoot_modal(page, recipe, out, state, styles, capture):
    name = recipe.get('name') or f"{_slug(recipe['page'])}__{_slug(recipe['modal'])}"
    for attempt in (1, 2):   # one retry: a laptop's network can suspend mid-run
        try:
            page.goto(recipe['page'])
            _settle(page, 0)
            capture.arm(page, recipe)
            _open_modal(page, recipe)
            break
        except Exception as e:  # noqa: BLE001 - one missing opener must not lose the rest of the run
            if attempt == 2:
                print(f'{name} [{state}]: not opened ({type(e).__name__}: {str(e).splitlines()[0]})')
                return
    size = page.evaluate(DIALOG_HEIGHT_JS)
    path = out / f'{name}__{state}.png'
    if styles:
        dump = path.with_suffix('.styles.json.gz')
        dump.write_bytes(gzip.compress(json.dumps(page.evaluate(STYLE_DUMP_JS)).encode()))
    tag = page.add_style_tag(content=UNCLIP_CSS)
    viewport = page.viewport_size   # a dialog taller than the window paints only what is on screen
    tall = page.locator('.modal.show .modal-dialog').bounding_box()['height'] + 120
    if tall > viewport['height']:
        page.set_viewport_size({'width': viewport['width'], 'height': int(tall)})
        page.wait_for_timeout(200)
    capture.before(page)
    page.locator('.modal.show .modal-dialog').screenshot(path=str(path))
    page.set_viewport_size(viewport)
    tag.evaluate('el => el.remove()')
    if not capture.after(path):
        return
    with (out / 'heights.tsv').open('a') as f:
        f.write(f"{name}\t{state}\t{size['height']}\t{size['natural']}\n")
    print(f"{path} height={size['height']} natural={size['natural']}")


def _shoot_page(page, recipe, out, state, capture):
    """A recipe without "modal": the viewport (or "element", or "full_page") after its steps."""
    name = recipe.get('name') or _slug(recipe['page'])
    viewport = page.viewport_size
    if recipe.get('viewport'):
        page.set_viewport_size({'width': recipe['viewport'][0], 'height': recipe['viewport'][1]})
    try:
        page.goto(recipe['page'])
        _settle(page, 0)
        capture.arm(page, recipe)
        _run_steps(page, recipe.get('steps', ()))
        page.evaluate('document.activeElement && document.activeElement.blur()')   # no focus ring or hover
        page.mouse.move(0, 0)                                                          # left by a click step
        capture.before(page)
        path = out / f'{name}__{state}.png'
        if recipe.get('element'):
            page.locator(f"{recipe['element']} >> visible=true").first.screenshot(path=str(path))
        else:
            page.screenshot(path=str(path), full_page=bool(recipe.get('full_page')))
    except Exception as e:  # noqa: BLE001 - one broken recipe must not lose the rest of the run
        print(f'{name} [{state}]: not shot ({type(e).__name__}: {str(e).splitlines()[0]})')
        return
    finally:
        page.set_viewport_size(viewport)
    if capture.after(path):
        print(path)


# Swaps names and emails for user_<hash8> pseudonyms in the page, before capture, so the real text
# never reaches a PNG. Emails by shape; names by the recipe's "redact" selectors; then every other
# occurrence of a swapped name (a path segment, a hover title). A MutationObserver redoes it for htmx swaps.
REDACT_JS = r"""(() => {
  if (window.__samRedact) return;
  const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
  const ATTRS = ['title', 'href', 'value', 'aria-label', 'alt', 'placeholder', 'data-bs-title', 'data-bs-original-title'];
  const names = new Map(), emails = new Map();
  let selectors = [], pattern = null, queued = false;
  const hash = s => { let h = 0x811c9dc5; for (const c of s) { h ^= c.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
                      return h.toString(16).padStart(8, '0'); };
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const name = s => { if (!names.has(s)) { names.set(s, 'user_' + hash(s)); pattern = null; } return names.get(s); };
  const known = () => pattern || (names.size && (pattern = new RegExp('(?<![A-Za-z0-9_])(' +
      [...names.keys()].sort((a, b) => b.length - a.length).map(esc).join('|') + ')(?![A-Za-z0-9_])', 'g')));
  const scrub = t => {
    t = t.replace(EMAIL, m => m.endsWith('@example.org') ? m
                  : (emails.has(m) || emails.set(m, 'user_' + hash(m) + '@example.org'), emails.get(m)));
    const re = known();
    return re ? t.replace(re, m => names.get(m)) : t;
  };
  const register = el => {
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) {
      const t = n.textContent.trim();
      if (!/[A-Za-z]/.test(t) || t.length < 2 || /^user_[0-9a-f]{8}(@example\.org)?$/.test(t)) continue;
      name(t);
      const parts = t.match(/^(.+?)\s*\(([^()\s]{3,})\)$/);   // "Jane Doe (jdoe)": each half alone, too
      if (parts) { name(parts[1]); name(parts[2]); }
    }
  };
  const run = () => {
    queued = false;
    if (!document.body) return;
    if (selectors.length) document.querySelectorAll(selectors.join(',')).forEach(register);
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) {
      if (n.parentElement && n.parentElement.closest('script, style')) continue;
      const t = scrub(n.textContent);
      if (t !== n.textContent) n.textContent = t;
    }
    for (const el of document.querySelectorAll(ATTRS.map(a => `[${a}]`).join(','))) {
      for (const a of ATTRS) {
        const v = el.getAttribute(a);
        if (v !== null) { const t = scrub(v); if (t !== v) el.setAttribute(a, t); }
      }
    }
    document.title = scrub(document.title);
  };
  const queue = () => { if (!queued) { queued = true; setTimeout(run, 0); } };
  new MutationObserver(queue).observe(document,
      {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  document.addEventListener('DOMContentLoaded', run);
  window.__samRedact = {
    select: list => { selectors = list || []; run(); },
    run,
    originals: () => [...names.keys(), ...emails.keys()],
  };
})();"""
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')


def ocr_leaks(text, originals):
    """Email-shaped tokens and swapped-out originals still legible in OCR ``text``."""
    flat = ' '.join(text.split()).lower()
    leaks = [m for m in _EMAIL.findall(text) if not m.lower().endswith('example.org')]
    return leaks + sorted(o for o in originals if len(o) >= 4 and ' '.join(o.split()).lower() in flat)


class Capture:
    """The production protocol around each shot: the write guard, redaction, and the OCR check."""

    def __init__(self, read_only=False, redact=False, verify=False):
        self.read_only, self.redact, self.verify = read_only, redact, verify
        self.originals, self.blocked, self.failed = set(), [], 0

    def attach(self, context):
        if self.read_only:
            context.route('**/*', self._guard)
        if self.redact:
            context.add_init_script(REDACT_JS)

    def _guard(self, route):
        req = route.request
        if req.method in ('GET', 'HEAD') and '/logout' not in req.url:
            route.continue_()
        else:
            self.blocked.append(f'{req.method} {req.url}')
            print(f'  blocked {req.method} {req.url}', flush=True)
            route.abort()

    def arm(self, page, recipe):
        if self.redact:
            page.evaluate('s => window.__samRedact.select(s)', recipe.get('redact', []))

    def before(self, page):
        if self.redact:
            page.evaluate('window.__samRedact.run()')
            self.originals.update(page.evaluate('window.__samRedact.originals()'))

    def after(self, path):
        """True to keep ``path``; a leak deletes it."""
        if not self.verify:
            return True
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / 'ocr.png'
            img = Image.open(path).convert('L')
            img.resize((img.width * 2, img.height * 2)).save(big)
            text = subprocess.run(['tesseract', str(big), 'stdout', '--psm', '11'],
                                  capture_output=True, text=True, check=True).stdout
        leaks = ocr_leaks(text, self.originals)
        if leaks:
            path.unlink()
            self.failed += 1
            print(f'{path}: DELETED, legible: {", ".join(leaks[:5])}', flush=True)
        return not leaks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--base-url', default='http://localhost:7050')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--page', action='append', dest='pages', help='repeatable; default: admin tables + gallery')
    ap.add_argument('--pages', choices=PAGE_SETS, dest='page_set',
                    help='a named page set, each page with its own steps')
    ap.add_argument('--layout', action='append', choices=LAYOUTS, dest='layouts')
    ap.add_argument('--theme', action='append', choices=THEMES, dest='themes')
    ap.add_argument('--expand', type=int, default=1, help='collapse groups to open per page (default 1)')
    ap.add_argument('--storage-state', help='skip the stub login and reuse this cookie file')
    ap.add_argument('--username', default='benkirk')
    ap.add_argument('--password', default='e2e')
    ap.add_argument('--styles', action='store_true', help='also dump computed styles as <name>.styles.json.gz')
    ap.add_argument('--headers', action='store_true',
                    help='report sort icons wrapped away from their header label; exit 1 if any')
    ap.add_argument('--wraps', action='store_true',
                    help='open every collapse and tab, report chevrons wrapped away from their text; exit 1 if any')
    ap.add_argument('--midword', action='store_true',
                    help='open every collapse and tab, report words split across two lines; exit 1 if any')
    ap.add_argument('--width', type=int, help="override each layout's viewport width (360: a small phone)")
    ap.add_argument('--compare', nargs=2, metavar=('BEFORE', 'AFTER'), type=Path,
                    help='diff two --styles folders and exit 1 on any difference (no browser)')
    ap.add_argument('--strict', action='store_true',
                    help='--compare: a difference in a custom property alone also fails')
    ap.add_argument('--px-tolerance', type=float, default=0.0, metavar='PX',
                    help='--compare: ignore px lengths this close (a live chart moves by 0.02px)')
    ap.add_argument('--compare-pixels', nargs=2, metavar=('BEFORE', 'AFTER'), type=Path,
                    help='diff same-named PNGs in two folders and exit 1 on any difference (no browser)')
    ap.add_argument('--element', metavar='SEL',
                    help='shoot the first visible match on each --page instead of the whole page')
    ap.add_argument('--step', action='append', dest='steps', default=[],
                    help='before the shot, in order: click:SEL, wait:SEL, reveal:SEL or fill:SEL=TEXT')
    ap.add_argument('--modal', help='opener to click on each --page; shoots .modal.show .modal-dialog')
    ap.add_argument('--recipes', type=Path,
                    help='JSON list of {name, page, steps, modal, inject: {file, into}}; without "modal" a page'
                         ' shot ({element, full_page, viewport, layout, theme, redact}); needs --out')
    ap.add_argument('--read-only', action='store_true',
                    help='abort every request but GET/HEAD (and logout); required off localhost')
    ap.add_argument('--redact', action='store_true',
                    help='swap emails, and names under each recipe\'s "redact" selectors, for pseudonyms before capture')
    ap.add_argument('--verify', action='store_true',
                    help='OCR each PNG (tesseract) and delete it if an email or a swapped name is legible')
    args = ap.parse_args(argv)
    if args.compare:
        return compare_dirs(*args.compare, strict=args.strict, px_tolerance=args.px_tolerance)
    if args.compare_pixels:
        return compare_pixels(*args.compare_pixels)
    [parse_step(s) for s in args.steps]   # fail before the browser starts
    recipes = json.loads(args.recipes.read_text()) if args.recipes else [
        {'page': url, 'steps': args.steps, 'modal': args.modal} for url in args.pages or []
    ] if args.modal else None
    if recipes:
        for r in recipes:
            [parse_step(s) for s in r.get('steps', ())]   # fail before the browser starts
        if args.out is None:
            ap.error('--out is required with --modal or --recipes')
    checks = args.headers or args.wraps or args.midword
    if args.out is None and not checks:
        ap.error('--out is required unless --compare, --compare-pixels, --headers, --wraps or --midword is given')
    host = re.sub(r'^https?://', '', args.base_url).split('/')[0].split(':')[0]
    if host not in ('localhost', '127.0.0.1') and not args.read_only:
        ap.error(f'{host} is not local: --read-only is required')
    if args.verify and not shutil.which('tesseract'):
        ap.error('--verify needs tesseract on PATH')
    capture = Capture(args.read_only, args.redact, args.verify)
    from playwright.sync_api import sync_playwright

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    problems = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        state = args.storage_state or _login(browser, args.base_url, args.username, args.password)
        for layout in args.layouts or LAYOUTS:
            width, height = LAYOUTS[layout]
            width = args.width or width
            for theme in args.themes or THEMES:
                context = browser.new_context(base_url=args.base_url, storage_state=state, color_scheme=theme,
                                              viewport={'width': width, 'height': height})
                capture.attach(context)
                context.add_cookies([{'name': 'sam_theme', 'value': theme, 'domain': host, 'path': '/'},
                                     {'name': 'sam_layout', 'value': layout, 'domain': host, 'path': '/'}])
                page = context.new_page()
                for recipe in recipes or ():
                    if recipe.get('layout', layout) != layout or recipe.get('theme', theme) != theme:
                        continue
                    if recipe.get('modal'):
                        _shoot_modal(page, recipe, args.out, f'{layout}-{theme}', args.styles, capture)
                    else:
                        _shoot_page(page, recipe, args.out, f'{layout}-{theme}', capture)
                named = PAGE_SETS[args.page_set] if args.page_set else [
                    (_slug(url), url, ()) for url in args.pages or DEFAULT_PAGES]
                for name, url, steps in () if recipes else named:
                    page.goto(url)
                    _settle(page, args.expand)
                    _run_steps(page, [*steps, *args.steps])
                    if args.out and args.element:
                        path = args.out / f'{name}__{layout}-{theme}.png'
                        target = page.locator(f'{args.element} >> visible=true').first
                        try:
                            target.wait_for(state='visible', timeout=8_000)
                        except Exception:  # noqa: BLE001 - one page without the element must not lose the run
                            print(f'{url} [{layout}-{theme}]: no visible {args.element}')
                            continue
                        box = target.bounding_box()
                        target.screenshot(path=str(path))
                        print(f"{path} {box['width']:.0f}x{box['height']:.0f}")
                    elif args.out:
                        path = args.out / f'{name}__{layout}-{theme}.png'
                        page.screenshot(path=str(path), full_page=True)
                        print(path)
                        if args.styles:
                            dump = path.with_suffix('.styles.json.gz')
                            dump.write_bytes(gzip.compress(json.dumps(page.evaluate(STYLE_DUMP_JS)).encode()))
                            print(dump)
                    if args.headers:  # last: it opens every collapsed group
                        for line in page.evaluate(HEADER_CHECK_JS):
                            problems += 1
                            print(f'{url} [{layout}-{theme}]: {line}')
                    for on, check in ((args.wraps, check_wraps), (args.midword, check_midword)):
                        if on:   # last: it opens every collapse and clicks every tab
                            for line in check(page):
                                problems += 1
                                print(f'{url} [{layout}-{theme} {width}px]: {line}', flush=True)
                context.close()
        browser.close()
    if checks:
        print(f'{problems} finding(s)')
    if capture.blocked:
        print(f'{len(capture.blocked)} request(s) blocked by --read-only')
    if capture.failed:
        print(f'{capture.failed} shot(s) deleted by --verify')
    return 1 if problems or capture.failed else 0


if __name__ == '__main__':
    sys.exit(main())
