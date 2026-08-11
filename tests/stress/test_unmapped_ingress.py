"""The catch-all is the widest door into ``xras_action_log``, and the least guarded.

``POST /actions`` refuses an oversized body with a 422 **before** recording — an explicit
pre-check in ``post_action``, written because a body we cannot record is a body we cannot
audit or replay. The unmapped-path catch-all has no equivalent: it reads whatever arrived
and hands it to ``_record``, relying entirely on ``_fit_payload``'s truncation. And
``MAX_CONTENT_LENGTH`` is 16 MB.

That is a second, independent path to the same two columns, reachable at *any* URL under
``/api/xras/`` rather than one, so it earns its own scenarios rather than inheriting
confidence from the actions tests.

Both columns are checked here, and they are guarded by different mechanisms — which is
the point of testing them together. ``raw_payload`` is utf8mb4 and survives by **charset**;
``outcome_reason`` is utf8mb3 and survives by ``_fit``'s **sanitising**. A change to
either one alone leaves this file half-red.
"""

import pytest

from .conftest import auth_headers
from .test_audit_row_survives import ASTRAL, TEXT_LIMIT

pytestmark = pytest.mark.stress


def test_unmapped_path_oversize_body(xras_client, action_log, scenario):
    """A body far past the column, POSTed to a path we do not implement.

    ⚠️ Unlike ``test_oversize_raw_payload``, nothing here refuses the request first.
    ``post_action`` checks the length and answers 422; ``unmapped_path`` records
    unconditionally, so ``_fit_payload`` is the *only* thing between a 16 MB body and a
    lost row. Worth its own test for exactly that reason.
    """
    body = 'x' * (TEXT_LIMIT * 2)

    resp = xras_client.post('/api/xras/v1/usage/by_month/2026', data=body,
                            content_type='application/json', headers=auth_headers())

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    stored = row['raw_payload']
    assert len(stored.encode()) <= TEXT_LIMIT
    assert 'CANNOT BE REPLAYED' in stored

    # The request line survives the truncation, at the front where it is readable —
    # it is the only part that answers "what did XRAS actually call?", which is the
    # entire reason this row exists.
    assert stored.startswith('POST /api/xras/v1/usage/by_month/2026')


def test_unmapped_path_astral(xras_client, action_log, scenario):
    """An emoji in both the path and the body of an unmapped call.

    Exercises the two guards against each other. The path is echoed into
    ``outcome_reason`` — ``varchar(255)``, still utf8mb3, so it survives only because
    ``_fit`` sanitises. The body lands in ``raw_payload`` — utf8mb4, so it survives
    intact. Before either guard, this lost the row and answered 500.
    """
    resp = xras_client.post(f'/api/xras/v1/things/{ASTRAL}',
                            data=f'{{"note":"{ASTRAL}"}}',
                            content_type='application/json', headers=auth_headers())

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # utf8mb3 column, sanitised: the row survives and still names the path.
    assert row['outcome_reason'] is not None
    assert ASTRAL not in row['outcome_reason']
    assert row['outcome_reason'].startswith('POST /api/xras/v1/things/')

    # utf8mb4 column, verbatim: the character XRAS actually sent is still here.
    assert ASTRAL in row['raw_payload']
