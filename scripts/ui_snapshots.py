#!/usr/bin/env python3
"""Screenshot pages in every layout x theme into a folder, for by-eye before/after review.

No baselines, no CI: run it on the base branch and on yours, then compare the two
folders (the middle ground in docs/plans/GALLERY_VISUAL_SNAPSHOTS.md). Needs the
[e2e] extra. Logs in through the stub form unless given --storage-state.
--styles also dumps every element's computed style; --compare proves "no visual change" for a
change that keeps the DOM. A restructure moves every element path, so prove that one with
--element SELECTOR on both sides and --compare-pixels.
--headers lists table headers whose sort icon wrapped onto a line of its own (no --out needed).
--modal OPENER (after any --step) or --recipes FILE shoots the opened dialog and prints its height.
--pages charts shoots every chart host, each with the steps its lazy tabs need.

    python scripts/ui_snapshots.py --out /tmp/before
    python scripts/ui_snapshots.py --out /tmp/after --page /admin/contracts --expand 2
    python scripts/ui_snapshots.py --styles --out /tmp/after && python scripts/ui_snapshots.py --compare /tmp/before /tmp/after
    python scripts/ui_snapshots.py --out /tmp/after --page /allocations/projects --element .filter-sidebar
    python scripts/ui_snapshots.py --compare-pixels /tmp/before /tmp/after
    python scripts/ui_snapshots.py --headers --layout desktop --layout mobile --theme light
    python scripts/ui_snapshots.py --out /tmp/m --page /admin/resources --modal '[data-bs-target="#createResourceModal"]'
    python scripts/ui_snapshots.py --out /tmp/m --recipes scripts/ui_snapshots_modals.json --layout desktop --layout mobile
    python scripts/ui_snapshots.py --out /tmp/c --pages charts --base-url http://localhost:5050
"""
import argparse
import gzip
import json
import re
import sys
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
    ('status-jobs-byproj', '/status/job-history', ['click:[data-bs-target$="-byproj"]']),
    ('jobs-timeline', _JOBS, ['wait:.jobs-timeline-chart svg']),
    ('jobs-byuser', _JOBS, ['click:[data-bs-target$="-byuser"]', 'wait:.jobs-user-pie svg']),
    ('jobs-byproj', _JOBS, ['click:[data-bs-target$="-byproj"]', 'wait:.jobs-user-pie svg']),
    ('jobs-wait', _JOBS, ['click:[data-bs-target$="-wait"]', 'wait:.jobs-histogram-chart svg']),
    ('jobs-sizes', _JOBS, ['click:[data-bs-target$="-sizes"]', 'wait:.jobs-histogram-chart svg']),
    ('details-compute', _DETAILS + 'Derecho', ['wait:#tab-usage-history svg']),
    ('details-byuser', _DETAILS + 'Derecho', ['click:[data-bs-target="#tab-usage-byuser"]']),
    ('details-disk', _DETAILS + 'Campaign+Store', []),
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


def parse_step(text):
    """``click:SEL`` / ``wait:SEL`` / ``reveal:SEL`` / ``scroll:SEL`` / ``fill:SEL=TEXT`` -> ``(verb, selector, text)``."""
    verb, _, rest = text.partition(':')
    if verb == 'fill':
        selector, sep, value = rest.rpartition('=')
        if not sep or not selector:
            raise ValueError(f'fill step needs SEL=TEXT: {text!r}')
        return verb, selector, value
    if verb not in ('click', 'wait', 'reveal', 'scroll') or not rest:
        raise ValueError(f'step must be click:SEL, wait:SEL, reveal:SEL, scroll:SEL or fill:SEL=TEXT: {text!r}')
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


def _shoot_modal(page, recipe, out, state, styles):
    name = recipe.get('name') or f"{_slug(recipe['page'])}__{_slug(recipe['modal'])}"
    for attempt in (1, 2):   # one retry: a laptop's network can suspend mid-run
        try:
            page.goto(recipe['page'])
            _settle(page, 0)
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
    page.locator('.modal.show .modal-dialog').screenshot(path=str(path))
    page.set_viewport_size(viewport)
    tag.evaluate('el => el.remove()')
    with (out / 'heights.tsv').open('a') as f:
        f.write(f"{name}\t{state}\t{size['height']}\t{size['natural']}\n")
    print(f"{path} height={size['height']} natural={size['natural']}")


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
                    help='JSON list of {name, page, steps, modal, inject: {file, into}}; needs --out')
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
    if args.out is None and not args.headers:
        ap.error('--out is required unless --compare, --compare-pixels or --headers is given')
    from playwright.sync_api import sync_playwright

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    problems = 0
    host = re.sub(r'^https?://', '', args.base_url).split('/')[0].split(':')[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        state = args.storage_state or _login(browser, args.base_url, args.username, args.password)
        for layout in args.layouts or LAYOUTS:
            width, height = LAYOUTS[layout]
            for theme in args.themes or THEMES:
                context = browser.new_context(base_url=args.base_url, storage_state=state,
                                              viewport={'width': width, 'height': height})
                context.add_cookies([{'name': 'sam_theme', 'value': theme, 'domain': host, 'path': '/'},
                                     {'name': 'sam_layout', 'value': layout, 'domain': host, 'path': '/'}])
                page = context.new_page()
                for recipe in recipes or ():
                    _shoot_modal(page, recipe, args.out, f'{layout}-{theme}', args.styles)
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
                context.close()
        browser.close()
    if args.headers:
        print(f'{problems} orphaned sort icon(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
