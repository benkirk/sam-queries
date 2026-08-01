#!/usr/bin/env python3
"""Generate the dark-mode NSF NCAR lockup from the light one.

    python3 scripts/make_reversed_logo.py

Reads  src/webapp/static/img/logo-ncar.png
Writes src/webapp/static/img/logo-ncar-reversed.png

Committed so the asset is reproducible rather than an unexplained binary, and
so the reasoning behind *which* parts get recoloured survives.

## Why only part of it moves

`logo-ncar.png` is not one mark. It is a 2457x621 lockup of three
separately-behaved parts, and measuring them (rather than assuming "it's a
navy logo") is what makes a narrow, brand-respecting recolour possible:

    [   0: 619]  NSF seal — gold gear, blue globe, white "NSF"   reads fine on dark
    [ 730: 747]  vertical rule, #404040                          invisible on dark
    [ 869:1271]  NCAR wave disc — blue disc, white waves         reads fine on dark
    [1386:2457]  "NCAR" + "OPERATED BY UCAR", brand blue         ~2.6:1 — the defect

Only the wordmark and the rule are touched. The NSF seal is left exactly as
the brand ships it; the wave disc is left because it already works.

This is also why a blanket `filter: invert()` was rejected for the CSS layer:
it would flatten the NSF seal and the wave disc into white silhouettes, which
is not an approved variant of either organization's mark.

The column spans are derived at runtime from the alpha channel rather than
hardcoded, so a re-cut source with different padding still works — but the
result is checked against the expected part count and fails loudly if the
lockup's structure has changed.

## Replacing this with the official asset

NSF NCAR publishes reversed variants (sundog.ucar.edu/page/10560, SSO-gated).
If the official file is preferred, save it over
`src/webapp/static/img/logo-ncar-reversed.png` and delete nothing else — the
templates reference it by name and no other code depends on how it was made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'src' / 'webapp' / 'static' / 'img' / 'logo-ncar.png'
DST = REPO / 'src' / 'webapp' / 'static' / 'img' / 'logo-ncar-reversed.png'

#: `--ncar-sky`. Also `--text-link` in the dark theme, so the wordmark and the
#: app's dark-mode links are the same blue.
WORDMARK_RGB = (0x42, 0xC0, 0xFF)

#: A mid neutral for the divider rule: visible on the dark band without
#: competing with the marks either side of it.
RULE_RGB = (0x8C, 0x98, 0xA6)

#: Minimum run of empty columns that separates two *parts* of the lockup.
#:
#: Measured, not guessed. In the shipped artwork the real gutters are ~115px
#: (seal|rule 115, rule|disc 126, disc|wordmark 115) while the largest gap
#: *inside* the wordmark is 39px (between "NCAR" and the "OPERATED BY UCAR"
#: baseline group). 60 sits in the middle of that gulf with room either side.
#: Too low and the wordmark shatters into three parts — which is exactly what
#: the part-count check below caught during development.
MIN_GUTTER = 60


def find_parts(alpha: np.ndarray) -> list[tuple[int, int]]:
    """Column spans of the lockup's inked parts, left to right."""
    inked = (alpha > 40).sum(axis=0) >= 2
    parts, start = [], None
    for i, on in enumerate(inked):
        if on and start is None:
            start = i
        elif not on and start is not None:
            parts.append((start, i))
            start = None
    if start is not None:
        parts.append((start, len(inked)))
    # Merge parts separated by less than a real gutter — the wordmark's
    # letters are individually inked and must stay one part.
    merged = [parts[0]]
    for lo, hi in parts[1:]:
        if lo - merged[-1][1] < MIN_GUTTER:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


def recolour(arr: np.ndarray, span: slice, rgb: tuple[int, int, int]) -> None:
    """Replace RGB across `span`, preserving alpha so antialiasing survives."""
    sub = arr[:, span]
    mask = sub[:, :, 3] > 0
    for channel, value in enumerate(rgb):
        plane = sub[:, :, channel]
        plane[mask] = value


def main() -> int:
    image = Image.open(SRC).convert('RGBA')
    arr = np.array(image).astype(np.int16)

    parts = find_parts(arr[:, :, 3])
    if len(parts) != 4:
        print(f'ERROR: expected 4 parts (seal, rule, disc, wordmark), found '
              f'{len(parts)}: {parts}\nThe lockup structure changed — re-read '
              f'this script\'s header before trusting the spans.', file=sys.stderr)
        return 1

    _seal, rule, _disc, wordmark = parts
    recolour(arr, slice(*wordmark), WORDMARK_RGB)
    recolour(arr, slice(*rule), RULE_RGB)

    Image.fromarray(arr.astype(np.uint8), 'RGBA').save(DST, optimize=True)
    print(f'wrote {DST.relative_to(REPO)}  ({DST.stat().st_size / 1024:.0f} KB)')
    print(f'  NSF seal   {_seal}  untouched')
    print(f'  rule       {rule}  -> #{RULE_RGB[0]:02x}{RULE_RGB[1]:02x}{RULE_RGB[2]:02x}')
    print(f'  wave disc  {_disc}  untouched')
    print(f'  wordmark   {wordmark}  -> '
          f'#{WORDMARK_RGB[0]:02x}{WORDMARK_RGB[1]:02x}{WORDMARK_RGB[2]:02x}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
