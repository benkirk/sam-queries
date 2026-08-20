"""The rendered window control on the three audit-style panels.

Transactions, Adjustments and the XRAS action log present their time window as
an `age_band_range` ladder rather than a bare From/To pair. These are the
markup-level guards, mirroring the equivalents for the disk-scans explorer in
``test_webapp_disk_scans.py``; the arithmetic behind the control lives in
``test_audit_window_filters.py``.
"""

import json
import re

import pytest

#: (page URL, form id). The XRAS page is the interesting one — it is the only
#: page carrying a second form with a start_date/end_date pair.
PANELS = [
    ('/allocations/transactions', 'tx-filters'),
    ('/allocations/adjustments', 'adj-filters'),
    ('/allocations/xras', 'xras-filters'),
]

PANEL_IDS = [url.rsplit('/', 1)[-1] for url, _ in PANELS]


def _body(auth_client, url, query=''):
    response = auth_client.get(url + query)
    assert response.status_code == 200, f'{url} did not render'
    return response.data.decode()


def _bands(body):
    """The ladder as the browser receives it — a JSON data block, never code."""
    block = re.search(
        r'<script type="application/json" class="ladder-range-bands">(.*?)</script>',
        body, re.S)
    assert block, 'no ladder data block on the page'
    return json.loads(block.group(1))


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_the_window_renders_as_a_ladder(auth_client, url, form_id):
    """The ladder travels as JSON so the browser only indexes it — no date
    arithmetic, and therefore no timezone reasoning, in JavaScript."""
    body = _body(auth_client, url)
    assert 'ladder-range-bands' in body
    assert len(re.findall(rf'id="{form_id}-age-lo"', body)) == 1
    assert len(re.findall(rf'id="{form_id}-age-hi"', body)) == 1
    assert 'aria-valuetext=' in body


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_audit_panels_render_exactly_one_named_date_pair(auth_client, url,
                                                         form_id):
    """Everything else in the control is unnamed UI that writes into these two.

    Two same-named controls in one form make ``form.elements[name]`` a
    RadioNodeList, and assigning ``.value`` to that silently does nothing — so
    the control would render, respond to drags, and filter nothing.

    This is also the guard for the macros' missing-parameter case: neither
    ``audit_filters`` nor ``xras_filters`` has a fallback branch, and
    ``ladder_range`` renders *nothing at all* when ``bands`` is empty, so a
    caller that forgot to pass ``age_bands`` would ship a panel with no date
    fields. Jinja will not raise for it; this will.
    """
    body = _body(auth_client, url)
    assert body.count('name="start_date"') == 1
    assert body.count('name="end_date"') == 1
    assert 'type="range"' in body
    assert 'name="age' not in body


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_the_resting_window_lands_on_a_whole_span(auth_client, url, form_id):
    """First load must NOT render the custom state. The pages pre-fill a
    30-day pair and the ladder has a band whose upper bound is exactly 30, so
    the control can name the window instead of shrugging at it. If this fails,
    that coupling broke — see test_the_default_window_is_a_whole_span."""
    body = _body(auth_client, url)
    assert 'ladder-range--custom' not in body
    assert 'Custom range' not in body


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_the_exact_dates_hide_behind_the_axis_ends(auth_client, url, form_id):
    """The From/To pair is still there, one click away, and both ends agree
    about it being shut."""
    body = _body(auth_client, url)
    panel = re.search(
        rf'<div class="ladder-range-exact([^"]*)"\s+id="{form_id}-age-exact"',
        body)
    assert panel, 'no exact-date panel rendered'
    assert 'd-none' in panel.group(1), (
        'the escape hatch is open at rest — it should reveal on an axis end')
    ends = re.findall(r'<button[^>]*class="ladder-range-end"[^>]*>', body)
    assert len(ends) == 2
    assert all('aria-expanded="false"' in e for e in ends)


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_the_open_ended_band_is_the_all_time_position(auth_client, url,
                                                      form_id):
    """The band that makes "everything" reachable without emptying a box by
    hand. Its older edge is `null` — which actions.js writes as '' — while its
    newer edge stays a real date, because the two thumbs are crossed."""
    bands = _bands(_body(auth_client, url))
    assert bands[-1]['start_date'] is None
    assert bands[-1]['end_date'] is not None
    assert bands[0]['end_date'] is not None


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_from_and_to_label_the_opposite_ends_from_the_slider(auth_client, url,
                                                             form_id):
    """The date boxes read in the OPPOSITE direction to the slider above them:
    the slider's axis is age (newest left), the boxes are calendar dates
    (earliest left). So `From` binds start_date — the OLDER bound — even though
    the high thumb feeds it. Pinned because a tidy-up that swapped them would
    be silently wrong in both directions at once."""
    body = _body(auth_client, url)
    from_field = re.search(
        rf'<label[^>]*for="{form_id}-age-from"[^>]*>([^<]*)</label>', body)
    assert from_field and from_field.group(1).strip() == 'From'
    assert re.search(
        rf'id="{form_id}-age-from"[^>]*name="start_date"', body), \
        'the From box no longer binds the older bound'
    assert re.search(
        rf'id="{form_id}-age-to"[^>]*name="end_date"', body)


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_typing_an_exact_date_marks_the_control_custom(auth_client, url,
                                                       form_id):
    """Without this hook the readout keeps naming the last span while the
    fields hold something else — the control would misdescribe the filter it is
    about to submit, which on an audit surface is worse than saying nothing."""
    body = _body(auth_client, url)
    assert body.count('data-action-change="ladder-range-typed"') == 2


@pytest.mark.parametrize('url,form_id', PANELS, ids=PANEL_IDS)
def test_mobile_swaps_the_thumbs_for_selects(auth_client, url, form_id):
    """Dragging two thumbs a few pixels apart is the worst case at 390px, so
    the phone layout is two selects — and it keeps the exact inputs visible,
    because there is no axis end left to reveal them."""
    body = _body(auth_client, url, '?layout=mobile')
    assert 'type="range"' not in body
    assert 'ladder-range-end' not in body
    assert len(re.findall(rf'id="{form_id}-age-lo"', body)) == 1
    panel = re.search(
        rf'<div class="ladder-range-exact([^"]*)"\s+id="{form_id}-age-exact"',
        body)
    assert panel and 'd-none' not in panel.group(1), (
        'mobile hid the date inputs behind a trigger it does not render')


#: The hidden window-carrying forms on /allocations/xras. Each owns its own
#: `days` field and its own fragment; the pairs are emitted by `window_pills`
#: INSIDE those fragments and bound back with `form=`.
XRAS_WINDOW_FORMS = ('xras-activity-filters', 'xras-accounts-filters')


def test_the_xras_page_keeps_one_date_pair_per_form(auth_client):
    """The one page where the RadioNodeList trap can actually bite.

    ``/allocations/xras`` carries three forms that care about dates: the panel
    (`#xras-filters`, whose pair is the ladder's, nested) and the two hidden
    window forms above, whose pairs are emitted by `window_pills` inside their
    fragments and bound with `form=`. Fragments are fetched and owned
    separately, so each form sees exactly one node — but that is a property of
    the markup, not a guarantee, so assert it.

    ⚠️ Counted **per form**, not per page. Counting per page happened to work
    while there was one hidden form and silently became wrong when a second
    was added — the trap is `form.elements[name]` returning a RadioNodeList,
    which is scoped to one form and says nothing about the document.
    """
    page = _body(auth_client, '/allocations/xras')
    assert page.count('name="start_date"') == 1
    for form_id in XRAS_WINDOW_FORMS:
        assert f'form="{form_id}"' not in page, (
            f'{form_id}\'s date pair leaked onto the page shell')
        block = re.search(rf'<form id="{form_id}".*?</form>', page, re.S)
        assert block, f'{form_id} is gone from the page shell'
        assert block.group(0).count('name="days"') == 1, (
            f'{form_id} must carry exactly one window field')

    fragment = auth_client.get(
        '/allocations/xras_pending_fragment').data.decode()
    assert fragment.count('name="start_date"') == 1
    assert fragment.count('form="xras-activity-filters"') >= 1, (
        'the pills\' dates are no longer bound to the activity form')
    # And the two controls address different reveal targets, so clicking one
    # cannot flip the other's aria-expanded (actions.js mirrors by selector).
    assert 'xras-activity-filters-custom' in fragment
    assert 'xras-filters-age-exact' in page
