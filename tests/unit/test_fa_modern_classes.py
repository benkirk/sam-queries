"""Font Awesome 7 modernization gate.

FA7 dropped the v4/v5 compatibility we relied on implicitly, so every icon
reference must use a modern style prefix (`fa-solid`/`fa-regular`/`fa-brands`)
and a canonical glyph name. This scans templates, static JS, and the webapp
package so a reintroduced legacy `fas`/`far` prefix or an old alias name
(`fa-edit`, `fa-times`, ...) fails CI instead of silently rendering blank.
"""
import re
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[2] / 'src' / 'webapp'
SUFFIXES = ('.html', '.js', '.py')

# Legacy alias glyph names retired at the 6->7 modernization (old -> canonical
# lives in the upgrade's rewrite; here we only need the old names to forbid).
LEGACY_GLYPHS = {
    'edit', 'trash-alt', 'times', 'times-circle', 'sync', 'sync-alt',
    'external-link-alt', 'university', 'info-circle', 'check-circle',
    'exclamation-circle', 'exclamation-triangle', 'plus-circle', 'user-circle',
    'map-marker-alt', 'sign-in-alt', 'sliders-h', 'shield-alt', 'share-alt',
    'level-down-alt', 'hdd', 'th', 'tasks', 'redo', 'history', 'magic', 'save',
    'search', 'balance-scale', 'calendar-alt', 'pencil-alt', 'thermometer-half',
    'users-cog', 'unlink', 'calendar-exclamation',
}

# A legacy prefix used as a class token: immediately before a fa- glyph or a
# Jinja expression that supplies one. The lookahead keeps English prose
# ("far more", "as far as") from matching.
_PREFIX = re.compile(r'\b(fas|far|fab)\b\s+(?:fa-|\{\{|\{%)')
_GLYPH = re.compile(r'(?<![\w-])fa-(' + '|'.join(map(re.escape, LEGACY_GLYPHS)) + r')(?![\w-])')


def _files():
    for path in WEBAPP_ROOT.rglob('*'):
        if path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(WEBAPP_ROOT).as_posix()
        if 'vendor/' in rel or '.min.' in path.name:
            continue
        yield path


def test_no_legacy_style_prefix():
    offenders = []
    for path in _files():
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if _PREFIX.search(line):
                offenders.append(f'{path.name}:{n}')
    assert not offenders, (
        'use fa-solid/fa-regular/fa-brands, not fas/far/fab (FA7): '
        + ', '.join(offenders))


def test_no_legacy_glyph_names():
    offenders = []
    for path in _files():
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            m = _GLYPH.search(line)
            if m:
                offenders.append(f'{path.name}:{n} (fa-{m.group(1)})')
    assert not offenders, (
        'FA5-era alias names were retired in FA7; use the canonical name: '
        + ', '.join(offenders))
