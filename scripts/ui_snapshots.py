#!/usr/bin/env python3
"""Screenshot pages in every layout x theme into a folder, for by-eye before/after review.

No baselines, no CI: run it on the base branch and on yours, then compare the two
folders (the middle ground in docs/plans/GALLERY_VISUAL_SNAPSHOTS.md). Needs the
[e2e] extra. Logs in through the stub form unless given --storage-state.

    python scripts/ui_snapshots.py --out /tmp/before
    python scripts/ui_snapshots.py --out /tmp/after --page /admin/contracts --expand 2
"""
import argparse
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_PAGES = [
    '/admin/resources', '/admin/resources?tab=machines', '/admin/resources?tab=queues',
    '/admin/organizations', '/admin/organizations?tab=institutions', '/admin/organizations?tab=areas',
    '/admin/contracts', '/admin/facilities', '/admin/account-requests', '/admin/events',
    '/dev/gallery',
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--base-url', default='http://localhost:7050')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--page', action='append', dest='pages', help='repeatable; default: admin tables + gallery')
    ap.add_argument('--layout', action='append', choices=LAYOUTS, dest='layouts')
    ap.add_argument('--theme', action='append', choices=THEMES, dest='themes')
    ap.add_argument('--expand', type=int, default=1, help='collapse groups to open per page (default 1)')
    ap.add_argument('--storage-state', help='skip the stub login and reuse this cookie file')
    ap.add_argument('--username', default='benkirk')
    ap.add_argument('--password', default='e2e')
    args = ap.parse_args()

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
                context.close()
        browser.close()


if __name__ == '__main__':
    main()
