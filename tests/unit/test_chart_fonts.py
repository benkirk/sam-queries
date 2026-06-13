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

# Importing charts.py triggers the module-level addfont() registration.
import webapp.dashboards.charts  # noqa: F401

FONT_DIR = (
    Path(__file__).resolve().parents[2]
    / 'src' / 'webapp' / 'static' / 'fonts' / 'poppins'
)


def test_poppins_ttfs_present():
    """The server-side ttf set matplotlib consumes must be committed."""
    ttfs = list(FONT_DIR.glob('*.ttf'))
    assert ttfs, f"no Poppins .ttf found under {FONT_DIR}"


def test_matplotlib_resolves_poppins():
    """findfont must resolve 'Poppins' to a real Poppins file, not fall back."""
    path = fm.findfont('Poppins', fallback_to_default=False)
    assert 'poppins' in path.lower(), f"matplotlib fell back to {path!r}"
