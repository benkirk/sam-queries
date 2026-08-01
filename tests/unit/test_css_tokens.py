"""
Raw-colour lint for the app's own CSS (src/webapp/static/css/).

A colour literal in component CSS is a colour that cannot follow the theme.
`variables.css` is where colour is *defined* — everywhere else it should be
referenced through a token, so that one edit in one file moves both themes.

ALLOWED is the ratchet, seeded at the state of `staging@bee0f4d` (the commit
this dark-mode work branched from). It pins the *exact* current debt per file;
the dark-mode commits shrink it. The assertion is equality, not `<=`, so a
file that gets *better* must also update its entry — the allowlist cannot go
stale, and a commit that silently trades one literal for another is caught.

This mirrors `test_template_csp_lint.py` deliberately, including the equality
ratchet and the "fixed files must be removed" half. Same idea one layer out:
that test stops inline script from coming back, this one stops hardcoded
colour from coming back. Its real value is *after* the dark-mode PR — it is
what prevents the next 119 literals.

What counts as a violation, in a declaration value, after `var(...)`
references are removed:

  - a hex literal            `#fff`, `#00357a`
  - `rgb()/rgba()/hsl()/hsla()` with a **literal** first argument
  - a bare CSS named colour  `white`, `navy`, ...

What does NOT count, and why:

  - `var(--anything)`               — the whole point; stripped before scanning
  - `rgba(var(--ncar-blue-rgb), .1)` — the house idiom for alpha on a brand
                                       colour. `--ncar-blue-rgb` and
                                       `--ncar-teal-rgb` exist precisely so
                                       this stays token-driven; the first
                                       argument is not a literal, so it passes.
  - `transparent` / `currentColor` / `inherit` / `none` — not colour values in
                                       the sense that matters here; they
                                       already follow whatever they sit on.
  - anything in `variables.css`     — the token file. Literals belong there,
                                       in both the `:root` and the
                                       `:root[data-bs-theme="dark"]` blocks.

Fixing a violation means adding or reusing a **tier-2 role token**
(`--surface-card`, `--text-primary`, `--border-default`, ...) — see
`docs/plans/DARK_MODE.md` § *The token layer*. Do not "fix" it by pointing at
a tier-1 primitive (`--ncar-navy`): those are brand constants and are
theme-invariant by design, so a component that references one directly has
the same problem in a different costume.
"""

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'static' / 'css'

#: The token file. Colour literals are its job.
TOKEN_FILE = 'variables.css'

COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)

#: `prop: value` pairs, anchored on a `{` or `;` so selectors are never read as
#: values. Custom properties are matched too — `--bs-card-bg: #fff` is exactly
#: the kind of literal this lint exists to find.
DECL_RE = re.compile(r'(?:^|[;{])\s*([-\w]+)\s*:\s*([^;{}]*)')

#: A `var()` reference, including a fallback. Removed before scanning, so a
#: token reference can never look like a literal.
VAR_RE = re.compile(r'var\(\s*[-\w]+\s*(?:,[^()]*)?\)')

HEX_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b')
#: rgb()/hsl() whose first argument is a literal number. `rgba(var(--x), .1)`
#: is token-driven and deliberately excluded.
FUNC_RE = re.compile(r'\b(?:rgba?|hsla?)\(\s*[\d.]')
#: Bare named colours. `transparent`/`currentColor` are intentionally absent.
NAMED_RE = re.compile(
    r'(?<![-\w])(?:white|black|red|green|blue|gray|grey|orange|gold|yellow|'
    r'silver|navy|teal|purple|pink|brown|cyan|magenta)(?![-\w])', re.I)

# Ratchet: seeded 2026-08-01 at staging@bee0f4d with 119 literals across six
# files. Each dark-mode commit lowers a number here; entries that reach zero
# are deleted rather than left at 0.
#
#   D4 -> the 12 `background: #fff` surfaces + 3 card variables
#   D6 -> status.css badges and the semantic border-lefts
#   D7 -> whatever the dark palette turns up
#
# dashboard.css will not reach zero in this PR: a large share of its 76 are
# `color: #fff` on saturated brand fills, which are correct in both themes and
# become `var(--text-on-brand)` — a token, so they leave the count — but the
# rest is ordinary pre-existing debt that dark mode is not obliged to retire.
# D6 cleared five of the six files outright. What is left in dashboard.css is
# not dark-mode debt: 22 sites that are shadows (`rgba(0,0,0,…)`), mask
# gradients, translucent white overlays on brand fills, and the navy dropdown
# panel's own `--bs-dropdown-*` values — all of which are either theme-neutral
# or deliberately pinned to a brand surface. They are left as ordinary
# pre-existing debt for a later pass rather than forced into tokens here.
ALLOWED = {
    # D2: -2 (--bs-card-bg, --bs-card-cap-bg)
    # D4: -49 (10 surfaces + 39 foregrounds -> --text-on-brand)
    # D6: -3  (greys and borders -> role tokens)
    'dashboard.css':   22,
}


def _violations(text):
    """Every `prop: value` in `text` whose value carries a colour literal."""
    found = []
    for prop, value in DECL_RE.findall(COMMENT_RE.sub('', text)):
        bare = VAR_RE.sub('', value)
        if HEX_RE.search(bare) or FUNC_RE.search(bare) or NAMED_RE.search(bare):
            found.append(f'{prop}: {value.strip()}')
    return found


def scan_css():
    """Return {filename: [violating declarations]}, excluding the token file."""
    found = {}
    for path in sorted(CSS_DIR.glob('*.css')):
        if path.name == TOKEN_FILE:
            continue
        hits = _violations(path.read_text())
        if hits:
            found[path.name] = hits
    return found


def test_no_new_raw_colours():
    found = scan_css()
    counts = {name: len(hits) for name, hits in found.items()}

    grew = {n: c for n, c in counts.items() if c > ALLOWED.get(n, 0)}
    shrank = {n: c for n, c in counts.items()
              if n in ALLOWED and c < ALLOWED[n]}
    gone = [n for n in ALLOWED if n not in counts]

    msg = []
    if grew:
        msg.append(
            'New hardcoded colours in component CSS. Add or reuse a tier-2 '
            'role token in variables.css (see this test\'s docstring and '
            'docs/plans/DARK_MODE.md) rather than raising ALLOWED:')
        for name in sorted(grew):
            msg.append(f'  {name}: found {counts[name]}, allowed '
                       f'{ALLOWED.get(name, 0)}')
            # Show the tail — the additions are almost always at the end of
            # whichever block was just edited.
            msg += [f'      {hit}' for hit in found[name][-8:]]
    if shrank:
        msg.append(
            'Fewer raw colours than the ratchet allows — good. Lower these '
            'in ALLOWED so the gain is locked in:')
        msg += [f'  {n}: {ALLOWED[n]} -> {c}' for n, c in sorted(shrank.items())]
    if gone:
        msg.append(
            'Files clean (or removed) but still listed in ALLOWED — ratchet '
            'them out:')
        msg += [f'  {n}' for n in sorted(gone)]

    assert not msg, '\n' + '\n'.join(msg)


def test_token_file_is_the_only_place_colour_is_defined():
    """The lint is meaningless if `variables.css` stops holding the palette.

    A refactor that moved tokens into a component file would leave the ratchet
    passing while defeating its purpose, so assert the token file is where the
    literals actually live.
    """
    token_literals = len(_violations((CSS_DIR / TOKEN_FILE).read_text()))
    assert token_literals >= 20, (
        f'{TOKEN_FILE} defines only {token_literals} colour literals — the '
        f'palette appears to have moved out of the token file, which makes '
        f'test_no_new_raw_colours a no-op guard.')


#: Tier-1 CSS token -> the `charts/theme.py` scalar that must hold the same
#: hex. The brand palette is duplicated because matplotlib cannot read CSS;
#: this pins the copies together. Direct precedent: `test_chart_theme.py`
#: already asserts `Theme.LIGHT` matches the module-level rcParams, "so a dark
#: theme never has to fight a stale global". Same argument, one layer out.
TIER1_PAIRS = {
    '--ncar-blue':       'UNITY_NCAR_BLUE',
    '--ncar-navy':       'UNITY_NCAR_NAVY',
    '--ncar-vermilion':  'UNITY_NCAR_VERMILION',
    '--ncar-orange':     'UNITY_NCAR_ORANGE',
    '--ncar-gold':       'UNITY_NCAR_GOLD',
    '--ncar-teal':       'UNITY_NCAR_TEAL',
    '--ncar-sky':        'UNITY_NCAR_SKY',
    '--ncar-light-blue': 'UNITY_NCAR_LIGHT_BLUE',
    '--ncar-space-blue': 'UNITY_NCAR_SPACE_BLUE',
    '--ncar-gray-light': 'UNITY_NCAR_GRAY_LIGHT',
    '--ncar-gray':       'UNITY_NCAR_GRAY',
}


def _css_tokens():
    """{token: value} for every custom property defined in variables.css."""
    text = COMMENT_RE.sub('', (CSS_DIR / TOKEN_FILE).read_text())
    return {name: value.strip()
            for name, value in DECL_RE.findall(text)
            if name.startswith('--')}


def test_tier1_matches_chart_palette():
    """The brand palette is triplicated; keep at least two copies honest.

    `variables.css` and `charts/theme.py` both spell out the NCAR brand
    colours — the CSS for the app chrome, the Python for matplotlib, which
    cannot read CSS. Consolidating them properly is a build-step question and
    is explicitly out of scope (docs/plans/DARK_MODE.md § *Keep the palette
    single-sourced*), so assert agreement instead.
    """
    from webapp.dashboards.charts import theme as chart_theme

    tokens = _css_tokens()
    mismatches = []
    for token, scalar in sorted(TIER1_PAIRS.items()):
        css_value = tokens.get(token)
        py_value = getattr(chart_theme, scalar, None)
        assert css_value is not None, f'variables.css lost {token}'
        assert py_value is not None, f'charts/theme.py lost {scalar}'
        if css_value.lower() != py_value.lower():
            mismatches.append(
                f'  {token} = {css_value}   !=   {scalar} = {py_value}')

    assert not mismatches, (
        '\nBrand palette drifted between variables.css and '
        'charts/theme.py:\n' + '\n'.join(mismatches))


def _dark_tokens():
    """{token: value} from the `:root[data-bs-theme="dark"]` block."""
    text = COMMENT_RE.sub('', (CSS_DIR / TOKEN_FILE).read_text())
    start = text.index(':root[data-bs-theme="dark"]')
    block = text[text.index('{', start) + 1:text.index('}', start)]
    return {n: v.strip() for n, v in DECL_RE.findall(block) if n.startswith('--')}


def test_dark_card_matches_chart_blend_target():
    """The dark card surface and the chart layer's blend target are one value.

    Charts are matplotlib SVGs rendered *inside* cards, and `Theme.DARK`
    blends its stacked-band shades, legend faces and segment edges toward the
    surface behind them. If `--surface-card` and `Theme.DARK.shade_toward`
    drift apart, every dark chart gets a faint rectangular halo where the
    figure meets the card — a defect that is easy to see and very hard to
    attribute.

    Picking the value once, in CSS, is what makes PR 4 (dark charts)
    mechanical. This test is what keeps it picked once.
    """
    from webapp.dashboards.charts.theme import Theme

    card = _dark_tokens().get('--surface-card')
    assert card, 'the dark block does not define --surface-card'

    for attr in ('shade_toward', 'legend_face', 'segment_edge'):
        value = getattr(Theme.DARK, attr)
        assert value.lower() == card.lower(), (
            f'Theme.DARK.{attr} = {value} but --surface-card = {card}. '
            f'Charts would blend toward a surface they no longer sit on — '
            f'change both together, in the same commit.')


def test_dark_block_redeclares_the_bootstrap_bridge():
    """Not redundant with the `:root` bridge — required.

    Bootstrap's own `[data-bs-theme=dark]` block redefines these same
    variables. Our selector is `:root[data-bs-theme="dark"]` (specificity
    0,2,0) precisely so it outranks Bootstrap's bare attribute selector
    (0,1,0). Drop any of them and cards, modals and tables silently revert to
    Bootstrap's grey #212529 ramp while the rest of the app uses the blue one —
    a mismatch that reads as "dark mode is buggy" rather than "a variable is
    missing".
    """
    dark = _dark_tokens()
    required = {'--bs-body-bg', '--bs-body-color', '--bs-border-color',
                '--bs-secondary-bg', '--bs-tertiary-bg'}
    missing = sorted(required - set(dark))
    assert not missing, (
        f'the dark block does not re-declare {missing}; Bootstrap\'s own dark '
        f'values will win for those')


def test_invariant_tokens_are_not_flipped():
    """`--text-on-brand` / `--surface-on-brand` must NOT appear in the dark
    block. White on a saturated NCAR-blue button is correct in both themes;
    "fixing" either into a theme-dependent value breaks every button and every
    pill badge in one of the two."""
    dark = _dark_tokens()
    flipped = sorted({'--text-on-brand', '--surface-on-brand'} & set(dark))
    assert not flipped, (
        f'{flipped} redefined in the dark block, but they are invariant by '
        f'design — see their definitions in variables.css')


def test_every_role_token_has_a_dark_value():
    """A tier-2 token with no dark counterpart keeps its LIGHT value on a dark
    page — a white surface or near-black text, silently. This is the check
    that catches a token added later and half-wired."""
    light = {n for n in _css_tokens()
             if n.startswith(('--surface-', '--text-', '--border-'))}
    invariant = {'--text-on-brand', '--surface-on-brand'}
    # `-rgb` companions are covered by their parents being covered.
    expected = {n for n in light if not n.endswith('-rgb')} - invariant
    dark = set(_dark_tokens())
    missing = sorted(expected - dark)
    assert not missing, (
        f'tier-2 tokens with no dark value (they will stay light on a dark '
        f'page): {missing}')


def test_role_tokens_exist():
    """The tier-2 contract, asserted by name.

    Component CSS is supposed to reach for these and nothing else for chrome.
    A rename that dropped one would otherwise surface as an unstyled surface
    somewhere far away, since an undefined `var()` silently resolves to
    nothing.
    """
    tokens = _css_tokens()
    required = {
        '--surface-page', '--surface-card', '--surface-raised',
        '--surface-secondary', '--surface-tertiary',
        '--text-primary', '--text-secondary', '--text-on-brand',
        '--border-default',
    }
    missing = sorted(required - set(tokens))
    assert not missing, f'tier-2 role tokens missing from variables.css: {missing}'


def test_value_named_tokens_are_gone():
    """The old value-named tokens must not come back.

    `--text-dark` resolving to near-white is the exact failure this rename
    exists to prevent, and it is invisible at the callsite. Re-adding any of
    these — even as a back-compat alias — reopens it.
    """
    retired = {'--bg-light', '--text-dark', '--bg-gray-light',
               '--bg-gray-medium', '--border-color', '--color-gray-muted'}
    tokens = set(_css_tokens())
    resurrected = sorted(retired & tokens)
    assert not resurrected, (
        f'value-named tokens redefined in variables.css: {resurrected}. '
        f'Use a tier-2 role token instead — see the file header.')

    # And nothing may reference them, with or without a var() fallback.
    referencing = []
    for path in sorted(CSS_DIR.glob('*.css')):
        text = COMMENT_RE.sub('', path.read_text())
        for name in retired:
            if re.search(r'var\(\s*' + re.escape(name) + r'\s*[,)]', text):
                referencing.append(f'{path.name}: var({name})')
    assert not referencing, (
        'references to retired value-named tokens:\n  '
        + '\n  '.join(referencing))


def test_ratchet_lists_only_real_files():
    """An ALLOWED entry for a file that does not exist would never fail."""
    present = {p.name for p in CSS_DIR.glob('*.css')}
    unknown = sorted(set(ALLOWED) - present)
    assert not unknown, (
        f'ALLOWED names CSS files that do not exist: {unknown}')
