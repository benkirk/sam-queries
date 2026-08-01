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

## Both layouts are pinned

Every non-empty case is rendered twice, at ``layout='desktop'`` and at
``layout='mobile'``, under snapshot keys ``<case>`` and ``<case>@mobile``.

The mobile half exists because the mobile pass is *tuning* work: figure
sizes, legend placement and font sizes get moved until they look right, and
without a pinned baseline "I nudged the pace chart" and "I broke the pace
chart" produce the same diff — none. It also makes the desktop invariant
enforceable in the same run: a mobile-tuning commit that moves a desktop
fingerprint has leaked, and that is the single most likely way this pass
regresses the 1,000+ desktop users it is not for.

Empty cases are layout-invariant by construction — ``is_empty()`` short-circuits
before ``make_figure()`` — so they are pinned once, and
``test_empty_state_is_layout_invariant`` proves the short-circuit really does
precede the geometry.
"""

import json
import os
from pathlib import Path

import pytest

from chart_fingerprint import svg_fingerprint
from chart_samples import CASES

SNAPSHOT = Path(__file__).parent / 'snapshots' / 'chart_fingerprints.json'

#: Suffix appended to a case id for its mobile rendering. Desktop keeps the
#: bare id so the existing snapshot keys — and every diff anyone has already
#: reviewed — stay stable.
MOBILE_SUFFIX = '@mobile'


def _render_all(app):
    """Render every case inside one app context, at both layouts.

    Several charts resolve modal routes through ``url_for``, so an application
    context is required even though nothing here touches the database.
    """
    out = {}
    with app.test_request_context('/'):
        for case_id, fn, args, kwargs in CASES:
            out[case_id] = svg_fingerprint(fn(*args, **kwargs))
            if case_id.endswith('.empty'):
                # Layout-invariant by construction; pinned once. The claim is
                # tested directly by test_empty_state_is_layout_invariant.
                continue
            out[case_id + MOBILE_SUFFIX] = svg_fingerprint(
                fn(*args, **kwargs, layout='mobile'))
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


def test_empty_state_is_layout_invariant(app):
    """Pinning empty cases once is only safe if the short-circuit really is
    layout-independent — i.e. ``is_empty()`` runs before ``make_figure()``.

    Cheap to assert, and it fails loudly if anyone ever moves the empty check
    below the figure creation to give the placeholder a layout-aware size.
    """
    empties = [c for c in CASES if c[0].endswith('.empty')]
    assert empties, 'no empty cases — the invariant below would be vacuous'
    with app.test_request_context('/'):
        for case_id, fn, args, kwargs in empties:
            desktop = fn(*args, **kwargs)
            mobile = fn(*args, **kwargs, layout='mobile')
            assert desktop == mobile, f'{case_id} placeholder differs by layout'


def test_mobile_renders_smaller_than_desktop(rendered):
    """The point of the axis, asserted as a property rather than a snapshot.

    Every chart's mobile figure must be *narrower* than its desktop one.
    This is what turns 9-11pt labels rendered at ~2-3px on a phone into
    labels rendered at roughly their nominal size, and it is the one claim
    that must hold for all fifteen charts no matter how the tuning moves.
    """
    pairs = [(cid, cid + MOBILE_SUFFIX) for cid in rendered
             if not cid.endswith(MOBILE_SUFFIX)
             and cid + MOBILE_SUFFIX in rendered]
    assert pairs, 'no desktop/mobile pairs rendered'

    wider = []
    for desktop_id, mobile_id in sorted(pairs):
        dw = rendered[desktop_id]['size'][0]
        mw = rendered[mobile_id]['size'][0]
        if mw >= dw:
            wider.append(f'{desktop_id}: desktop {dw}pt -> mobile {mw}pt')
    assert not wider, 'mobile figure is not narrower:\n  ' + '\n  '.join(wider)
