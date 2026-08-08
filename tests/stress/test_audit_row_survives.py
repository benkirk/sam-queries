"""The audit write is the one thing this table cannot afford to lose.

Two columns took untrusted, unbounded values straight from the wire. Under
``STRICT_TRANS_TABLES`` — confirmed on for the test container and standard for MySQL 8 —
an oversized value does **not** truncate. It raises ``1406 Data too long``, the INSERT
or UPDATE fails, and the row is gone.

That is worse than any wrong value, because every other failure mode still leaves
something to triage from. ``_fit()`` already existed for exactly this reason on
``action_type`` and ``request_number``, whose docstring says it outright: *"an audit
write that 500s is the one failure mode this table cannot afford"*. Two columns were
missed.

Both scenarios below construct genuinely oversized input rather than monkeypatching a
width, because the interesting part is whether the sizes are **reachable** from a legal
payload — and for ``error_messages``, it is reachable while ``raw_payload`` still fits.
"""

import json

import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.stress

PATH = '/api/xras/v1/actions'

#: ``TEXT`` — 65,535 **bytes**, not characters. The column is utf8mb3.
TEXT_LIMIT = 65_535


def _resources(count, first_key=990_000, bad_amount=False):
    """*count* resources with distinct unmapped keys, in the **wire** shape.

    Distinct matters twice over: ``ActionErrors`` deduplicates, so identical keys —
    or identical bad amounts — would collapse to one message, and the size of the
    accumulated list is the whole point.

    Minimal entries (no ``comments``, no ``actionResourceId``) because the scenario
    turns on the ratio between what a resource costs in the body and what it yields in
    errors. Padding the entry would hide the amplification.
    """
    return [{'resourceRepositoryKey': first_key + i,
             'awardedAmount': f'x{first_key + i}' if bad_amount else '1'}
            for i in range(count)]


def _compact(payload):
    """Serialise the way a broker would, not the way ``json.dumps`` defaults to.

    ``json.dumps``'s default ``', '``/``': '`` separators add ~40% to a
    resource-heavy body, which is enough to make the payload overflow first and mask
    the error-list overflow entirely.
    """
    return json.dumps(payload, separators=(',', ':'))


def test_oversize_error_messages(xras_client, action_log, dispatching, scenario,
                                 committing_route):
    """1,000 resources on a **New**, each with an unmapped key *and* a bad amount.

    ⚠️ The action type matters, and finding out why was the useful part.

    ``resourceRepositoryKey`` is a long field name, so one unmapped key costs about as
    many bytes in the body as the message it produces — measured **1.00x** on the
    Supplement path, where a failed key resolution ``continue``s before the amount is
    read. On that path an oversized error list is simply not reachable: the body hits
    its own limit first.

    ``NewHandler._plan_allocations`` calls ``resolve_resource`` **and**
    ``transaction_amount`` unconditionally, then checks both — so one resource can
    yield **two** distinct messages. Measured **1.79x**: 59,090 bytes of body
    producing 105,999 bytes of messages, the body 10% clear of its limit while the
    list is 62% past it.

    So the guard is not defensive: it is on the only path that reaches the condition,
    and it took the corrected wire field name to see which path that was.
    """
    import sam.xras.handlers  # noqa: F401  — registers all six

    payload = {
        'actionType': 'New',
        'requestNumber': 'STRESS01',
        'allocationType': 'Small',
        'requestTitle': 'Oversize error list',
        'actionBeginDate': '2026-01-01',
        'actionEndDate': '2026-12-31',
        'resources': _resources(1_000, bad_amount=True),
        'roles': [],
    }
    body = _compact(payload)

    assert len(body.encode()) < TEXT_LIMIT, (
        'the body must fit its own column, or this only tests raw_payload')

    resp = xras_client.post(PATH, data=body, content_type='application/json',
                            headers=auth_headers())

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # The point: the row exists at all, and the stored list is inside the column.
    stored = row['error_messages']
    assert stored is not None, 'the audit row lost its error list'
    assert len(stored.encode()) <= TEXT_LIMIT

    # Truncation is lossy, so it must be self-announcing. A silently short list would
    # send an operator hunting for errors that were never written down.
    assert 'truncated' in stored.lower()

    # ⚠️ The 422 BODY is not truncated. It is the wire contract XRAS administrators
    # read, and the accumulated list is the entire point of it — only the stored copy
    # is bounded.
    returned = json.loads(resp.data)['result']['errors']
    assert len(returned) > 1_900, f'expected ~2 messages per resource, got {len(returned)}'
    assert len('\n'.join(returned).encode()) > TEXT_LIMIT


def test_the_supplement_path_cannot_reach_the_same_condition():
    """The contrast, measured rather than assumed — and the reason the scenario above
    is a New.

    A failed key resolution ``continue``s before ``transaction_amount`` runs, so
    Supplement yields exactly one message per resource against an entry of almost
    identical length. The body always hits its limit first.
    """
    from sam.xras import errors as e

    count = 1_400
    body = _compact({'actionType': 'Supplement', 'requestNumber': 'S',
                     'allocationType': 'Small',
                     'resources': _resources(count), 'roles': []})
    messages = '\n'.join(e.no_resource_for_key(str(990_000 + i))
                         for i in range(count))

    ratio = len(messages.encode()) / len(body.encode())
    assert 0.95 < ratio < 1.05, f'measured {ratio:.2f}x — the contrast has moved'
    assert len(body.encode()) > TEXT_LIMIT


def test_oversize_raw_payload(xras_client, action_log, scenario):
    """A body larger than ``raw_payload`` must still leave a row behind.

    This is the **first** write, before parsing and before dispatch, so failing it
    loses the action entirely — there is no later opportunity to record anything.

    Capture-only is left ON: the body never reaches a handler, and this is about
    ``_record``, not about dispatch.
    """
    payload = {
        'actionType': 'Supplement',
        'requestNumber': 'STRESS02',
        'allocationType': 'Small',
        'resources': _resources(2_400),
        'roles': [],
    }
    body = _compact(payload)
    assert len(body.encode()) > TEXT_LIMIT, 'this scenario needs an oversized body'

    resp = xras_client.post(PATH, data=body, content_type='application/json',
                            headers=auth_headers())

    assert resp.status_code == scenario['http']
    row = action_log.one()

    stored = row['raw_payload']
    assert len(stored.encode()) <= TEXT_LIMIT

    # ⚠️ A truncated payload is **not replayable**, and the stored bytes say so rather
    # than looking like a normal capture. Replay reads them back through the schema;
    # truncated JSON would fail to parse, which is at least loud — but an operator
    # deciding whether to click Replay deserves to know first.
    assert 'CANNOT BE REPLAYED' in stored

    # And the action is REFUSED rather than applied. A body we cannot record is a body
    # we cannot audit or replay, so processing it would write allocations against a
    # record that does not survive. The error list says so in the 422, where XRAS's
    # own panel renders it.
    assert row['status'] == scenario['expect']
    assert 'exceeds' in row['error_messages']
    assert 'was not applied' in row['error_messages']

    # `action_type` is NULL: the body was refused before it was ever parsed, so we
    # genuinely do not know it — the same honest NULL the malformed-JSON path writes.
    assert row['action_type'] is None


def test_the_widths_match_the_database(app):
    """The ORM's declared widths must equal the DDL's, or the guards guard nothing.

    ``http_status`` was ``Integer`` in the ORM against ``SMALLINT UNSIGNED`` in the
    DDL — harmless in practice, but the kind of drift that makes a width guard
    computed from the ORM quietly wrong.
    """
    from sqlalchemy import inspect

    from sam.integration.xras import XrasActionLog
    from webapp.extensions import db

    with app.app_context():
        columns = {c['name']: c for c in
                   inspect(db.engine).get_columns('xras_action_log')}

    for name, expected in (('remote_actor', 11), ('action_type', 32),
                           ('request_number', 30), ('projcode_result', 30),
                           ('processed_by', 35)):
        orm_width = getattr(XrasActionLog, name).type.length
        assert orm_width == expected, f'{name}: ORM says {orm_width}'
        assert columns[name]['type'].length == expected, f'{name}: DDL disagrees'

    # SMALLINT UNSIGNED tops out at 65,535; every code we answer is 3 digits.
    assert str(columns['http_status']['type']).upper().startswith('SMALLINT')
    assert str(XrasActionLog.http_status.type).upper().startswith('SMALLINT')
