"""Structural fingerprint of a rendered chart SVG.

Why not golden SVGs: matplotlib's SVG output is unstable three independent
ways. ``<dc:date>`` is a wall-clock stamp, so byte-equality breaks between two
runs of *identical* code; clip-path ids derive from a per-process hash salt;
and path data, font metrics and ``bbox_inches='tight'`` dimensions shift across
matplotlib patch releases. The first two are suppressible, the third is not,
and would make every matplotlib bump a 16-file golden churn.

So we fingerprint only what the application actually contracts on.

Two extraction details are load-bearing, and both were got wrong in an early
draft of the design (see docs/plans/CHART_ARCHITECTURE.md, Appendix C):

1. **Drill links are ``<a>`` elements only.** ``svg.fonttype`` defaults to
   ``'path'``, so matplotlib renders text as glyph outlines: a single chart
   carries ~400 ``xlink:href`` attributes of which only ~30 are drill links.
   The rest are ``<use>`` glyph references and *hash-salted* clip-path ids,
   which change between processes. Taking every ``xlink:href`` would be both
   93% noise and run-unstable — the exact failure "no golden SVGs" avoids.

2. **Text comes from the comments matplotlib emits.** Because glyphs are
   paths, there are **zero** ``<text>`` elements to read. Matplotlib writes the
   source string as an XML comment immediately before each glyph group
   (``<!-- 62.5% -->``), which is both stable and human-readable in a diff.
   If ``svg.fonttype`` is ever set to ``'none'``, real ``<text>`` elements
   appear and are picked up too — the extractor handles both.
"""

import re

#: ``<a ... xlink:href="...">`` — matplotlib also emits ``target="_blank"``,
#: and newer versions may drop the ``xlink:`` prefix, so accept both spellings.
_A_HREF_RE = re.compile(r'<a\s[^>]*?(?:xlink:)?href="([^"]*)"')

#: The text matplotlib writes before each glyph group. The XML prolog carries
#: no comments and the DOCTYPE is not one, so every match is a rendered string.
_COMMENT_RE = re.compile(r'<!--(.*?)-->', re.S)

#: Both ``style="fill: #rrggbb"`` and ``fill="#rrggbb"`` spellings appear.
_FILL_RE = re.compile(r'fill:\s*(#[0-9a-f]{3,8})|fill="(#[0-9a-f]{3,8})"', re.I)

_SVG_OPEN_RE = re.compile(r'<svg\b[^>]*>')
_WIDTH_RE = re.compile(r'\bwidth="([\d.]+)pt"')
_HEIGHT_RE = re.compile(r'\bheight="([\d.]+)pt"')

#: Real ``<text>`` elements, present only if ``svg.fonttype='none'``.
_TEXT_EL_RE = re.compile(r'<text\b[^>]*>(.*?)</text>', re.S)


def _collapse_runs(items):
    """Drop consecutive duplicates, preserving order.

    The palette contract is the *sequence* of colours (including the
    deliberately reversed one on the user/proj stacked area). Every glyph group
    restates the text colour, so without this the fill list is hundreds of
    repetitions of the same chrome colour and the data colours are invisible in
    a diff.
    """
    out = []
    for it in items:
        if not out or out[-1] != it:
            out.append(it)
    return out


def svg_fingerprint(rendered: str) -> dict:
    """Structural fingerprint of a chart's return value.

    Handles both branches of the chart contract: an SVG string, or the
    ``_empty_state`` placeholder div, which is pinned verbatim (it is short,
    and its exact classes are part of the contract).
    """
    if rendered is None:
        return {'kind': 'none'}

    text = rendered.strip()
    if not text:
        return {'kind': 'empty-string'}

    if '<svg' not in text:
        # The no-data placeholder fragment.
        return {'kind': 'placeholder', 'html': text}

    open_tag = _SVG_OPEN_RE.search(text)
    open_tag = open_tag.group(0) if open_tag else ''
    w = _WIDTH_RE.search(open_tag)
    h = _HEIGHT_RE.search(open_tag)

    def _dim(m):
        # 1 dp — enough to catch a bbox_inches='tight' layout shift without
        # tripping on float noise between matplotlib patch releases.
        return round(float(m.group(1)), 1) if m else None

    fills = [(a or b).lower() for a, b in _FILL_RE.findall(text)]

    labels = [c.strip() for c in _COMMENT_RE.findall(text)]
    labels += [t.strip() for t in _TEXT_EL_RE.findall(text)]

    return {
        'kind': 'svg',
        # The drill contract: ordered, <a> elements only.
        'drill_hrefs': _A_HREF_RE.findall(text),
        # Labels, legend entries, ticks, autopct strings — in draw order.
        'labels': labels,
        # The palette contract, consecutive duplicates collapsed.
        'fills': _collapse_runs(fills),
        'counts': {
            'a': text.count('<a '),
            'path': text.count('<path '),
            'use': text.count('<use '),
            'g': text.count('<g '),
            'text': text.count('<text '),
        },
        'size': [_dim(w), _dim(h)],
    }
