"""Four reasons an action parks, and the two columns that now tell them apart.

⚠️ **This file used to document a gap; it now proves the fix.** Every scenario here
once asserted that the row could *not* answer "why did this park", which was the
evidence behind the ``service`` / ``outcome_reason`` verdict in
``docs/xras/incoming/implemented/XRAS_STRESS_AND_SCHEMA.md``. The columns landed, so the assertions are
inverted: what was proof of a deficiency is now proof of a feature.

``dispatch.py`` argued for exactly this in its own docstring — *"knowing that an
Extension parked because Extension was disabled, rather than because nothing matched,
is the difference between a two-minute triage and a long one."* It has carried
``service`` and ``reason`` on ``DispatchResult`` since Sprint C; only the columns were
missing, and the app log holding them in the meantime is ephemeral in k8s.
"""

import pytest

from .conftest import post_action as _post

pytestmark = pytest.mark.stress


def _action(action_type='Supplement', request_number='PARK0001', **extra):
    payload = {'actionType': action_type, 'requestNumber': request_number,
               'allocationType': 'Small', 'resources': [], 'roles': []}
    payload.update(extra)
    return payload


#: The columns an operator can actually filter on in the XRAS dashboard. Two parked
#: rows agreeing on all of these are indistinguishable in practice, not just in theory.
_TRIAGE_COLUMNS = ('status', 'action_type', 'request_number', 'http_status',
                   'error_messages', 'projcode_result', 'service',
                   'outcome_reason')


def _triage_view(row):
    return {k: row[k] for k in _TRIAGE_COLUMNS}


def test_park_no_service(xras_client, action_log, dispatching, scenario):
    """``Adjustment`` against a project that does not exist — no selector matches."""
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, _action('Adjustment', 'NOSUCH01'))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']
    assert row['error_messages'] is None
    assert row['projcode_result'] is None
    assert row['processed_time'] is not None

    # A NULL `service` IS the answer here: nothing matched, so no service was ever
    # selected. That is a different row from one where a service WAS selected and
    # then something stopped it running.
    assert row['service'] is None
    assert 'no service matches' in row['outcome_reason']


def test_park_unknown_action_type(xras_client, action_log, dispatching, scenario,
                                  snapshot_project):
    """``Date Adjustment`` — a real wire type with no serviceable, from real bytes.

    ⚠️ The project must **exist**, or this collapses into ``test_park_no_service``:
    with a nonexistent project every type falls off the selector chain, so the row
    would be identical for the wrong reason. ``snapshot_project`` is a committed row,
    which is what the route's own session can see.

    The payload body is the real ``date_adjustment_*`` shape — dates, no resources —
    retargeted at a project the route can find. Only the referent moves.
    """
    import sam.xras.handlers  # noqa: F401

    from xras_helpers import load_fixture
    payload = dict(load_fixture('date_adjustment_uazn0052_manual.json'))
    payload['requestNumber'] = snapshot_project.projcode

    resp = _post(xras_client, payload)

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']
    assert row['error_messages'] is None

    # Recorded verbatim: the audit trail's job is to say what actually arrived, and
    # this is the column an operator filters on when a run of these shows up.
    assert row['action_type'] == 'Date Adjustment'

    assert row['service'] is None
    assert 'no service matches' in row['outcome_reason']


def test_park_disabled_type(xras_client, action_log, dispatching, scenario, app,
                            snapshot_project):
    """The same shape, parked for a completely different reason.

    ⚠️ The project must **exist**, or ``select_service`` returns ``None`` before the
    allowlist is ever consulted and this silently becomes the previous scenario.
    That is the failure mode this whole file is about, reappearing one level up: two
    causes, one indistinguishable outcome — except here it would be two *tests*
    measuring the same thing.
    """
    import sam.xras.handlers  # noqa: F401

    app.config['XRAS_ACTIONS_ENABLED'] = 'Extension'
    resp = _post(xras_client, _action('Adjustment', snapshot_project.projcode))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # The lever case, and the sharp one: an operator who narrows the allowlist at
    # 3am can now confirm from the table that it took effect. Note `service` is
    # populated here and NULL in the unmatched case — a service WAS selected, and
    # then config stopped it running.
    assert row['service'] == 'adjust'
    assert 'XRAS_ACTIONS_ENABLED' in row['outcome_reason']


def test_a_disabled_park_and_an_unmatched_park_are_distinguishable(
        xras_client, action_log, dispatching, app, snapshot_project):
    """The former evidence, now the acceptance test.

    ⚠️ This assertion is **inverted from what it was**. It used to read
    ``unmatched == disabled``, and passing it was the problem: the equality was the
    proof that four parking causes produced one indistinguishable row, and it is
    what the ``service`` / ``outcome_reason`` verdict rested on. The columns landed,
    so it now asserts the difference.
    """
    import sam.xras.handlers  # noqa: F401

    # Cause 1: no project, so no selector matches at all.
    _post(xras_client, _action('Adjustment', 'NOSUCH01'))
    # Cause 2: the project exists so `adjust` IS selected, and then the triage
    # lever stops it. Same action_type, same status, same everything else.
    app.config['XRAS_ACTIONS_ENABLED'] = 'Extension'
    _post(xras_client, _action('Adjustment', snapshot_project.projcode))

    rows = action_log.rows()
    assert len(rows) == 2
    unmatched, disabled = _triage_view(rows[0]), _triage_view(rows[1])

    assert unmatched != disabled, (
        'the two parking causes are byte-identical again — service and '
        'outcome_reason have stopped being written')
    assert unmatched['outcome_reason'] != disabled['outcome_reason']

    # Everything an operator could filter on BEFORE the columns is still identical,
    # which is what makes the two new ones load-bearing rather than incidental.
    #
    # `request_number` is excluded because the two causes require different
    # projects by construction — one must not exist. In production both causes
    # occur against real projcodes, where it carries no signal either.
    old = ('status', 'action_type', 'http_status', 'error_messages',
           'projcode_result')
    assert {k: unmatched[k] for k in old} == {k: disabled[k] for k in old}


def test_park_transfer_by_design(xras_client, action_log, dispatching, scenario,
                                 snapshot_project):
    """Transfer parks deliberately — and is the one park with a discriminator.

    Two, in fact, and only one of them was there before C.1a: ``action_type`` is
    dedicated to Transfer, and ``projcode_result`` now names the project.
    """
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, _action('Transfer', snapshot_project.projcode))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # The triage query `transfer.py`'s own docstring promises.
    assert row['action_type'] == 'Transfer'
    # C.1a: the projcode reaches the row rather than only the app log.
    assert row['projcode_result'] == snapshot_project.projcode

    # `NOT_IMPLEMENTED_REASON` — the sentence written specifically for whoever reads
    # this row at 3am with no context — now reaches the row rather than stopping at
    # the log. Bounded at 255, so match its opening rather than the whole thing.
    assert row['service'] == 'transfer'
    assert row['outcome_reason'].startswith(
        'Transfer is deliberately not serviced by this integration')

    # Still NULL, and the two columns are not interchangeable: `error_messages`
    # means "the 422 body XRAS received", and a parked action answers 200.
    assert row['error_messages'] is None
