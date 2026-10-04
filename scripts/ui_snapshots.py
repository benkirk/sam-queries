#!/usr/bin/env python3
"""Screenshot pages in every layout x theme into a folder, for by-eye before/after review.

No baselines, no CI: run it on the base branch and on yours, then compare the two
folders (the middle ground in docs/plans/GALLERY_VISUAL_SNAPSHOTS.md). Needs the
[e2e] extra. Logs in through the stub form unless given --storage-state.
--styles also dumps every element's computed style; --compare proves "no visual change".

    python scripts/ui_snapshots.py --out /tmp/before
    python scripts/ui_snapshots.py --out /tmp/after --page /admin/contracts --expand 2
    python scripts/ui_snapshots.py --styles --out /tmp/after && python scripts/ui_snapshots.py --compare /tmp/before /tmp/after
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


def compare_dirs(before_dir, after_dir, top=20):
    """Print the differences between two --styles folders; 1 when any element differs."""
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
        diffs = diff_styles(*(json.loads(gzip.decompress(f.read_bytes())) for f in (pa, pb)))
        elements = len({d[0] for d in diffs})
        print(f'{name}: {elements} elements differ' if diffs else f'{name}: same')
        for el, prop, va, vb in diffs[:top]:
            print(f'    {el}  {prop}: {va} -> {vb}')
        failed += bool(diffs)
    print(f'{failed} of {len(names)} captures differ')
    return 1 if failed else 0


def _settle(page, expand):
    try:
        page.wait_for_load_state('networkidle', timeout=15_000)
    except Exception:  # noqa: BLE001 - a long-poll page never idles; shoot what loaded
        pass
    triggers = page.locator('.tab-pane.active [data-bs-toggle="collapse"]:visible, '
                            '#contractsTable [data-bs-toggle="collapse"]:visible, '
                            '#facilities-pane [data-bs-toggle="collapse"]:visible')
    for i in range(min(expand, triggers.count())):
        triggers.nth(i).click()
        page.wait_for_timeout(400)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--base-url', default='http://localhost:7050')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--page', action='append', dest='pages', help='repeatable; default: admin tables + gallery')
    ap.add_argument('--layout', action='append', choices=LAYOUTS, dest='layouts')
    ap.add_argument('--theme', action='append', choices=THEMES, dest='themes')
    ap.add_argument('--expand', type=int, default=1, help='collapse groups to open per page (default 1)')
    ap.add_argument('--storage-state', help='skip the stub login and reuse this cookie file')
    ap.add_argument('--username', default='benkirk')
    ap.add_argument('--password', default='e2e')
    ap.add_argument('--styles', action='store_true', help='also dump computed styles as <name>.styles.json.gz')
    ap.add_argument('--compare', nargs=2, metavar=('BEFORE', 'AFTER'), type=Path,
                    help='diff two --styles folders and exit 1 on any difference (no browser)')
    args = ap.parse_args(argv)
    if args.compare:
        return compare_dirs(*args.compare)
    if args.out is None:
        ap.error('--out is required unless --compare is given')
    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
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
                for url in args.pages or DEFAULT_PAGES:
                    page.goto(url)
                    _settle(page, args.expand)
                    path = args.out / f'{_slug(url)}__{layout}-{theme}.png'
                    page.screenshot(path=str(path), full_page=True)
                    print(path)
                    if args.styles:
                        dump = path.with_suffix('.styles.json.gz')
                        dump.write_bytes(gzip.compress(json.dumps(page.evaluate(STYLE_DUMP_JS)).encode()))
                        print(dump)
                context.close()
        browser.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
