"""
Regression guard for server-side chart fonts.

matplotlib renders the status-dashboard charts (charts.py) server-side and
expects the Poppins brand font. matplotlib's font_manager can only read
ttf/otf/afm — NOT the .woff2 the browser uses — so charts.py keeps a separate
ttf set under static/fonts/poppins/ and registers it via addfont().

These TTFs were once deleted as "unreferenced" during the CSP/woff2 vendoring
(commit f93957b), which silently degraded every chart to DejaVu Sans and spammed
`findfont: Font family 'Poppins' not found`. This test fails loudly if that
recurs.
"""

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

# Importing the charts package triggers the module-level addfont() registration.
import webapp.dashboards.charts as charts

FONT_DIR = (
    Path(__file__).resolve().parents[2]
    / 'src' / 'webapp' / 'static' / 'fonts' / 'poppins'
)


def test_poppins_ttfs_present():
    """The server-side ttf set matplotlib consumes must be committed."""
    ttfs = list(FONT_DIR.glob('*.ttf'))
    assert ttfs, f"no Poppins .ttf found under {FONT_DIR}"


def test_charts_font_dir_resolves():
    """The path charts/ computes from __file__ must be the real font dir.

    Not redundant with the tests below: when `charts.py` became
    `charts/__init__.py`, its `__file__`-relative walk-up silently pointed one
    directory too shallow. Nothing failed — `findfont` still resolved, because
    the developer happened to have Poppins installed in ~/Library/Fonts. In the
    container it would have fallen straight back to DejaVu.
    """
    assert charts._FONT_DIR == FONT_DIR
    assert charts._FONT_DIR.exists(), f'{charts._FONT_DIR} does not exist'


def test_repo_ttfs_are_registered_with_matplotlib():
    """Our committed ttfs must actually be in matplotlib's font list.

    This is the assertion `test_matplotlib_resolves_poppins` should always
    have made. `findfont` succeeding proves only that *some* Poppins is
    reachable — on a developer machine with Poppins in ~/Library/Fonts it
    passes even when `addfont()` registered nothing at all, which is exactly
    how the `__file__` walk-up regression escaped. In the container, where no
    system Poppins exists, that same break falls back to DejaVu.

    Checking registration rather than resolution is also indifferent to which
    of several same-family files matplotlib happens to score highest.
    """
    registered = {Path(f.fname).resolve() for f in fm.fontManager.ttflist}
    ours = {p.resolve() for p in FONT_DIR.glob('*.ttf')}
    missing = ours - registered
    assert not missing, (
        f'{len(missing)} repo Poppins ttf(s) not registered with matplotlib: '
        f'{sorted(p.name for p in missing)} — addfont() did not run over '
        f'{FONT_DIR}.')


def test_matplotlib_resolves_poppins():
    """findfont must resolve 'Poppins' rather than falling back."""
    path = fm.findfont('Poppins', fallback_to_default=False)
    assert 'poppins' in path.lower(), f'matplotlib fell back to {path!r}'


def test_rcparams_font_family_is_poppins():
    """Guards against a refactor moving the rcParams block into a function
    that never gets called — every chart would silently render in DejaVu."""
    assert plt.rcParams['font.family'][0] == 'Poppins'
