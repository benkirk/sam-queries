"""Characterization gate for the chart-architecture refactor.

Pins a structural fingerprint of every chart's rendered output (see
``chart_fingerprint.svg_fingerprint`` for what is extracted and why). Written
*before* any source moves, so the refactor has something to move against.

The discipline this enforces (docs/plans/CHART_ARCHITECTURE.md § The
discipline) is **declared visual change, not zero visual change**: PR 1 is
allowed to change how charts look, but only in commits that say so. A
fingerprint delta in a commit whose stated purpose was "extract theme.py" is a
bug until proven otherwise.

Regenerate after an intentional change, then review the diff in that commit:

    CHART_FINGERPRINT_REGEN=1 pytest tests/unit/test_chart_fingerprints.py

## Every layout and every theme is pinned

Every non-empty case is rendered once per (layout, theme) combination. Desktop
and light are the identity, so they keep the bare case id and every other
rendering adds suffixes — ``<case>@mobile``, ``<case>@dark``,
``<case>@mobile@dark``. Both lists are read off the code (the chart classes and
``charts.theme.THEMES``), so a fourth profile or a third theme pins itself.

Keeping desktop+light bare is what preserves the existing snapshot keys, and
with them every diff anyone has already reviewed.

The non-identity halves exist because these passes are *tuning* work: figure
sizes, legend placement, font sizes and now chrome colours get moved until they
look right, and without a pinned baseline "I nudged the pace chart" and "I broke
the pace chart" produce the same diff — none. It also makes the desktop+light
invariant enforceable in the same run: a mobile-tuning or dark-tuning commit
that moves a desktop light fingerprint has leaked, and that is the single most
likely way these passes regress the users they are not for.

Empty cases are invariant on both axes by construction — ``is_empty()``
short-circuits before ``make_figure()`` — so they are pinned once, and
``test_empty_state_is_axis_invariant`` proves the short-circuit really does
precede the geometry.
"""

import json
import os
from pathlib import Path

import pytest

from chart_fingerprint import svg_fingerprint
from chart_samples import CASES

SNAPSHOT = Path(__file__).parent / 'snapshots' / 'chart_fingerprints.json'


def extra_layouts():
    """Every declared layout except desktop, which keeps the bare case id.

    Read off the chart classes rather than listed here, so adding a profile to
    ``layout.profile()`` pins it in this gate without touching this file. The
    bare-id convention for desktop is what keeps the existing snapshot keys —
    and every diff anyone has already reviewed — stable.
    """
    from webapp.dashboards import charts

    names = set()
    for fn in vars(charts).values():
        cls = getattr(fn, 'chart_class', None)
        if cls is not None and cls.LAYOUTS:
            names |= set(cls.LAYOUTS)
    return tuple(sorted(names - {'desktop'}))


def extra_themes():
    """Every declared theme except light, which keeps the bare case id.

    Read off ``charts.theme.THEMES`` for the same reason ``extra_layouts``
    reads the chart classes: the vocabulary lives in one place and this gate
    follows it.
    """
    from webapp.dashboards.charts.theme import THEMES

    return tuple(sorted(set(THEMES) - {'light'}))


def _renderings():
    """``[(suffix, kwargs), ...]`` — every (layout, theme) combination.

    Desktop and light contribute no suffix and no kwarg, so the identity
    rendering is spelled exactly as it was before either axis existed.
    """
    out = []
    for layout in ('desktop',) + extra_layouts():
        for theme in ('light',) + extra_themes():
            suffix = ''
            kwargs = {}
            if layout != 'desktop':
                suffix += f'@{layout}'
                kwargs['layout'] = layout
            if theme != 'light':
                suffix += f'@{theme}'
                kwargs['theme'] = theme
            out.append((suffix, kwargs))
    return out


def _render_all(app):
    """Render every case inside one app context, at every layout and theme.

    Several charts resolve modal routes through ``url_for``, so an application
    context is required even though nothing here touches the database.
    """
    out = {}
    renderings = _renderings()
    with app.test_request_context('/'):
        for case_id, fn, args, kwargs in CASES:
            if case_id.endswith('.empty'):
                # Invariant on both axes by construction; pinned once. The
                # claim is tested directly by test_empty_state_is_axis_invariant.
                out[case_id] = svg_fingerprint(fn(*args, **kwargs))
                continue
            for suffix, axis_kwargs in renderings:
                out[case_id + suffix] = svg_fingerprint(
                    fn(*args, **kwargs, **axis_kwargs))
    return out


@pytest.fixture(scope='module')
def rendered(app):
    return _render_all(app)


def test_case_ids_are_unique():
    ids = [c[0] for c in CASES]
    assert len(ids) == len(set(ids)), 'duplicate case id in CASES'


def test_every_chart_is_covered():
    """Every public generator must appear in at least one case.

    Without this, adding chart #17 silently escapes the gate.
    """
    from webapp.dashboards import charts

    public = {n for n in dir(charts) if n.startswith('generate_')}
    # Match by identity, not `__name__`: charts bound through `chart_view` are
    # closures whose `__name__` is the wrapper's, not the public alias's.
    cased = {id(fn) for _id, fn, _a, _k in CASES}
    covered = {n for n in public if id(getattr(charts, n)) in cased}
    assert public - covered == set(), f'charts with no fingerprint case: {public - covered}'


def test_chart_fingerprints_match_snapshot(rendered, app):
    if os.environ.get('CHART_FINGERPRINT_REGEN'):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(rendered, indent=2, sort_keys=True) + '\n')
        pytest.skip(f'regenerated {SNAPSHOT}')

    assert SNAPSHOT.exists(), (
        f'{SNAPSHOT} missing — regenerate with CHART_FINGERPRINT_REGEN=1')
    expected = json.loads(SNAPSHOT.read_text())

    assert set(rendered) == set(expected), (
        'case set changed; regenerate the snapshot and review the diff')

    # Compare per-case so a failure names the chart rather than dumping 40.
    mismatched = [cid for cid in sorted(expected) if rendered[cid] != expected[cid]]
    if mismatched:
        first = mismatched[0]
        pytest.fail(
            f'{len(mismatched)} chart(s) changed: {mismatched}\n\n'
            f'--- {first} expected ---\n'
            f'{json.dumps(expected[first], indent=2, sort_keys=True)[:2000]}\n'
            f'--- {first} actual ---\n'
            f'{json.dumps(rendered[first], indent=2, sort_keys=True)[:2000]}')


def test_fingerprints_are_stable_across_runs(app):
    """Guards the fingerprint itself.

    Two renders of identical code must fingerprint identically. If a
    hash-salted clip-path id or the ``<dc:date>`` stamp ever leaks into the
    extractor, this fails while the snapshot test still passes — which is the
    only way to tell "the gate broke" from "the charts changed".
    """
    a = _render_all(app)
    b = _render_all(app)
    assert a == b


def test_empty_states_are_placeholders(rendered):
    """The 20 empty-state sites must degrade to a div, never raise or emit SVG.

    These render into htmx fragments, so an exception 500s a card rather than
    showing "no data".
    """
    for case_id, fp in rendered.items():
        if case_id.endswith('.empty'):
            assert fp['kind'] == 'placeholder', f'{case_id} did not short-circuit'
            assert 'text-muted' in fp['html']


def test_empty_state_is_axis_invariant(app):
    """Pinning empty cases once is only safe if the short-circuit really is
    axis-independent — i.e. ``is_empty()`` runs before ``make_figure()``.

    Cheap to assert, and it fails loudly if anyone ever moves the empty check
    below the figure creation to give the placeholder an axis-aware size or a
    themed colour.
    """
    empties = [c for c in CASES if c[0].endswith('.empty')]
    assert empties, 'no empty cases — the invariant below would be vacuous'
    with app.test_request_context('/'):
        for case_id, fn, args, kwargs in empties:
            identity = fn(*args, **kwargs)
            for suffix, axis_kwargs in _renderings():
                if not suffix:
                    continue
                other = fn(*args, **kwargs, **axis_kwargs)
                assert identity == other, (
                    f'{case_id} placeholder differs at {suffix.lstrip("@")}')


def test_theme_changes_colour_but_never_geometry(rendered):
    """The theme axis is chrome and palette only — asserted as a property.

    Two claims, and each catches a different real mistake:

    **Geometry must not move.** ``bbox_inches='tight'`` sizes the figure from
    text extents, which a colour cannot change. If a dark fingerprint's size
    ever differs, something structural leaked into the theme branch — a legend
    that only renders in one theme, an extra artist — and the two themes have
    silently stopped being the same chart.

    **Colour must move.** The failure this guards is the whole reason PR 4
    exists: ``Theme.DARK`` was defined, live and reachable for a full release
    while *nothing applied it*, so a chart asked for in dark rendered exactly
    the light bytes. A theme that changes nothing is indistinguishable from a
    theme that is not plumbed in, and only this direction of the assertion can
    tell them apart.
    """
    moved, unchanged = [], []
    for theme in extra_themes():
        for base_id in [c for c in rendered if '@' not in c]:
            themed = rendered.get(f'{base_id}@{theme}')
            if themed is None or themed['kind'] != 'svg':
                continue               # empty-state cases are pinned once
            if themed['size'] != rendered[base_id]['size']:
                moved.append(f'{base_id}@{theme}: {rendered[base_id]["size"]} '
                             f'-> {themed["size"]}')
            if themed['fills'] == rendered[base_id]['fills']:
                unchanged.append(f'{base_id}@{theme}')

    assert not moved, 'theme moved the geometry:\n  ' + '\n  '.join(moved)
    assert not unchanged, (
        'theme did not change any fill — is it reaching the artists?\n  '
        + '\n  '.join(unchanged))


def _pairs(rendered, name):
    suffix = '@' + name
    out = [(cid, cid + suffix) for cid in rendered
           if '@' not in cid and cid + suffix in rendered]
    assert out, f'no desktop/{name} pairs rendered'
    return sorted(out)


def test_mobile_renders_smaller_than_desktop(rendered):
    """The point of the axis, asserted as a property rather than a snapshot.

    Every chart's mobile figure must be *narrower* than its desktop one.
    This is what turns 9-11pt labels rendered at ~2-3px on a phone into
    labels rendered at roughly their nominal size, and it is the one claim
    that must hold for all fifteen charts no matter how the tuning moves.
    """
    wider = []
    for desktop_id, mobile_id in _pairs(rendered, 'mobile'):
        dw = rendered[desktop_id]['size'][0]
        mw = rendered[mobile_id]['size'][0]
        if mw >= dw:
            wider.append(f'{desktop_id}: desktop {dw}pt -> mobile {mw}pt')
    assert not wider, 'mobile figure is not narrower:\n  ' + '\n  '.join(wider)


def test_tablet_falls_between_mobile_and_desktop(rendered):
    """The band is between the other two, so the figure must be as well.

    Non-strict at the desktop end on purpose: the pie family declares tablet
    *as* its desktop figure (see `PieChart.LAYOUTS`), because a pie's tight
    bbox is already narrower than every card that renders one. Strict at the
    mobile end, where no family has a reason to tie.
    """
    bad = []
    for desktop_id, tablet_id in _pairs(rendered, 'tablet'):
        dw = rendered[desktop_id]['size'][0]
        tw = rendered[tablet_id]['size'][0]
        mw = rendered[desktop_id + '@mobile']['size'][0]
        if not mw < tw <= dw:
            bad.append(f'{desktop_id}: mobile {mw}pt, tablet {tw}pt, '
                       f'desktop {dw}pt')
    assert not bad, 'tablet figure is out of order:\n  ' + '\n  '.join(bad)
