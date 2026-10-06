"""The chart frame: every chart host is built from ``fragments/chart_bits.html``.

A chart is a named ``<figure>``, its controls come from one pill macro, and it loads
behind one placeholder. Before the frame there were seven placeholders and about
fifteen charts a screen reader could not name.
"""
import re

from _paths import REPO_ROOT

TEMPLATES = REPO_ROOT / 'src' / 'webapp' / 'templates'
BITS = 'dashboards/fragments/chart_bits.html'

#: A chart's SVG arriving in a template.
_CHART = re.compile(r'\{\{-?\s*(chart_svg|pie_svg|pie_chart|fair_share_chart|sunbursts\[[^\]]+\]\.\w+)'
                    r'\s*\|\s*safe')
_OPEN = re.compile(r'\{%-?\s*call chart_figure\(|<figure\b[^>]*aria-label=')
_CLOSE = re.compile(r'\{%-?\s*endcall\s*-?%\}|</figure>')

#: Fragments whose chart is named by the figure of the host that loads them.
NAMED_BY_HOST = {
    # Swapped into the Used ring's <figure> in allocations/partials/_resource_charts.html.
    'dashboards/allocations/partials/used_sunburst.html',
}


def _templates():
    return {str(p.relative_to(TEMPLATES)): p.read_text() for p in TEMPLATES.rglob('*.html')}


def test_every_chart_is_emitted_inside_a_named_figure():
    unnamed = []
    for name, src in _templates().items():
        if name == BITS or name in NAMED_BY_HOST:
            continue
        for match in _CHART.finditer(src):
            before = src[:match.start()]
            opened = max((m.end() for m in _OPEN.finditer(before)), default=-1)
            closed = max((m.end() for m in _CLOSE.finditer(before)), default=-1)
            if opened <= closed:
                unnamed.append(f'{name}:{before.count(chr(10)) + 1} {match.group(1)}')
    assert not unnamed, ('a chart outside chart_figure (or a <figure aria-label>):\n  '
                         + '\n  '.join(unnamed))


def test_the_exemptions_still_emit_a_chart():
    templates = _templates()
    assert all(_CHART.search(templates[name]) for name in NAMED_BY_HOST)


def test_chart_loaders_use_the_one_placeholder():
    """No hand-rolled spinner line saying a chart, a calendar or usage is loading."""
    hand_rolled = re.compile(r'fa-spinner[^\n]*>\s*Loading (chart|usage|calendar|timeline)')
    found = [name for name, src in _templates().items()
             if name != BITS and hand_rolled.search(src)]
    assert not found, f'use chart_loading() from {BITS}: {found}'


def test_a_selected_pill_is_pressed_for_a_screen_reader():
    macro = _templates()[BITS]
    assert 'aria-pressed="{{ \'true\' if p.on else \'false\' }}"' in macro
    # ...and no chart fragment keeps a hand-written htmx pill beside the macro.
    pill = re.compile(r'class="btn btn-outline-secondary\s*\{%\s*if[^>]*?hx-get', re.S)
    charts = [name for name, src in _templates().items() if _CHART.search(src) and name != BITS]
    assert not [name for name in charts if pill.search(_templates()[name])]
