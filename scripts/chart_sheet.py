#!/usr/bin/env python3
"""Render every chart sample in every layout x theme to SVG, plus one index.html contact sheet.

The fingerprint gate cannot see path geometry, strokes, opacity or <title> text; this can.
Output is byte-stable (fixed hash salt and date), so --compare proves a drawing refactor
changed nothing. No database: the samples are tests/unit/charts/chart_samples.py CASES.

    python scripts/chart_sheet.py --out /tmp/charts-before
    python scripts/chart_sheet.py --out /tmp/charts-after --case pace
    python scripts/chart_sheet.py --compare /tmp/charts-before /tmp/charts-after
"""
import argparse
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYOUTS = ('desktop', 'tablet', 'mobile')
THEMES = ('light', 'dark')
PAGE = """<!doctype html><meta charset="utf-8"><title>Chart sheet</title>
<style>
 body {{ font: 14px system-ui; margin: 16px; }}
 h2 {{ font-size: 15px; margin: 28px 0 6px; }}
 .row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }}
 figure {{ margin: 0; padding: 8px; border: 1px solid #ccd; border-radius: 4px; }}
 figure.dark {{ background: #1c2333; color: #dde; }}
 figcaption {{ font-size: 11px; opacity: .7; margin-bottom: 4px; }}
 img {{ display: block; max-width: 100%; }}
</style>
{body}
"""


def _app():
    for key in ('SAM_DB_USERNAME', 'SAM_DB_PASSWORD', 'SAM_DB_SERVER'):
        os.environ.setdefault(key, 'unused')   # create_app wants them; nothing connects
    os.environ['FLASK_CONFIG'] = 'testing'     # in-process chart cache, never Redis
    os.environ.setdefault('FLASK_SECRET_KEY', 'chart-sheet')
    os.environ.setdefault('FLASK_ACTIVE', '1')
    os.environ['SOURCE_DATE_EPOCH'] = '0'      # matplotlib stamps the SVG with a date
    for sub in ('src', 'tests', 'tests/unit/charts'):
        sys.path.insert(0, str(ROOT / sub))
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['svg.hashsalt'] = 'chart-sheet'   # else clip-path ids are random
    from webapp.run import create_app
    return create_app()


def render(out: Path, only: str = '') -> int:
    app = _app()
    from chart_samples import CASES
    out.mkdir(parents=True, exist_ok=True)
    body, count = [], 0
    with app.test_request_context('/'):
        for case_id, fn, args, kwargs in CASES:
            if only and only not in case_id:
                continue
            states = [('desktop', 'light')] if case_id.endswith('.empty') else [
                (lay, thm) for lay in LAYOUTS for thm in THEMES]
            body.append(f'<h2>{html.escape(case_id)}</h2><div class="row">')
            for layout, theme in states:
                svg = fn(*args, **kwargs, layout=layout, theme=theme)
                name = f'{case_id}__{layout}-{theme}.svg'
                if not svg.lstrip().startswith('<?xml') and '<svg' not in svg[:300]:
                    name = name[:-4] + '.html'   # the empty-state fragment
                (out / name).write_text(svg)
                count += 1
                inner = (f'<img src="{html.escape(name)}" alt="">' if name.endswith('.svg')
                         else svg)
                body.append(f'<figure class="{theme}"><figcaption>{layout} / {theme}</figcaption>'
                            f'{inner}</figure>')
            body.append('</div>')
    (out / 'index.html').write_text(PAGE.format(body='\n'.join(body)))
    print(f'{count} renderings -> {out / "index.html"}')
    return 0


def compare(before: Path, after: Path) -> int:
    """Print the renderings whose bytes differ between two folders; 1 when any does."""
    names = sorted({p.name for d in (before, after) for p in d.glob('*__*.*')})
    if not names:
        print(f'no renderings in {before} or {after}')
        return 1
    changed = 0
    for name in names:
        a, b = before / name, after / name
        if not (a.exists() and b.exists()):
            print(f'{name}: only in {before if a.exists() else after}')
        elif a.read_bytes() != b.read_bytes():
            print(f'{name}: differs ({a.stat().st_size} -> {b.stat().st_size} bytes)')
        else:
            continue
        changed += 1
    print(f'{changed} of {len(names)} renderings differ')
    return 1 if changed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path)
    ap.add_argument('--case', default='', help='only cases whose id contains this')
    ap.add_argument('--compare', nargs=2, metavar=('BEFORE', 'AFTER'), type=Path)
    args = ap.parse_args(argv)
    if args.compare:
        return compare(*args.compare)
    if args.out is None:
        ap.error('--out is required unless --compare is given')
    return render(args.out, args.case)


if __name__ == '__main__':
    sys.exit(main())
