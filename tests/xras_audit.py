"""The ``action_log`` fixture — shared by the API tier and the stress tier.

Lives in its own module rather than in either tier's conftest because both need it and
its two hazards are the kind that must not drift between copies: the gap-lock deadlock
and the self-FK delete order, both documented on the fixture itself.

Import it where it is wanted::

    from xras_audit import action_log   # noqa: F401  — pytest resolves it by name

``tests/`` is on ``sys.path`` (the same route ``from factories import ...`` takes), so
the bare module name is correct.
"""

import pytest


@pytest.fixture
def action_log(app, monkeypatch):
    """Read and clean up the audit rows the route commits on its own connection.

    The route deliberately commits **outside** ``db.session`` so the row survives a
    handler rollback (see ``webapp/api/xras/actions.py``). That is exactly why the
    suite's per-test SAVEPOINT cannot undo these writes: they land on a different
    connection and are already committed, so they must be deleted explicitly or they
    leak into the shared xdist database.

    Rows are identified by **capturing the ids the route mints**, not by a
    ``id > watermark`` range. Two reasons, both learned the hard way:

    * A ``DELETE ... WHERE id > n`` predicate takes an open-ended gap lock up to the
      supremum, which collides with every other worker's ``INSERT`` — a reliable
      ``1213 Deadlock found`` under ``-n auto``. Deleting by primary key takes record
      locks on exactly the rows involved.
    * A watermark read on a table other workers are concurrently inserting into is
      racy in the other direction too: this test would *see* their rows.
    """
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from webapp.api.xras import actions as actions_module
    from webapp.extensions import db

    minted = []
    original_record = actions_module._record

    def _capturing_record(**kwargs):
        log_id = original_record(**kwargs)
        minted.append(log_id)
        return log_id

    monkeypatch.setattr(actions_module, '_record', _capturing_record)

    class Reader:
        """The rows this test's request(s) created, as detached plain dicts."""

        def rows(self):
            if not minted:
                return []
            with app.app_context(), Session(db.engine) as session:
                found = session.execute(
                    select(XrasActionLog)
                    .where(XrasActionLog.xras_action_log_id.in_(minted))
                    .order_by(XrasActionLog.xras_action_log_id)
                ).scalars().all()
                return [
                    {
                        'id': r.xras_action_log_id,
                        'remote_actor': r.remote_actor,
                        'status': r.status,
                        'action_type': r.action_type,
                        'request_number': r.request_number,
                        'action_id': r.action_id,
                        'service': r.service,
                        'outcome_reason': r.outcome_reason,
                        'raw_payload': r.raw_payload,
                        'error_messages': r.error_messages,
                        'projcode_result': r.projcode_result,
                        'processed_time': r.processed_time,
                        'received_time': r.received_time,
                        # Sprint B columns. http_status separates the two things
                        # status='failed' conflates (400 vs 422); processed_by and
                        # source_action_id are the replay chain.
                        'http_status': r.http_status,
                        'processed_by': r.processed_by,
                        'source_action_id': r.source_action_id,
                    }
                    for r in found
                ]

        def one(self):
            rows = self.rows()
            assert len(rows) == 1, f'expected exactly 1 audit row, got {len(rows)}'
            return rows[0]

        def by_id(self, log_id):
            for row in self.rows():
                if row['id'] == log_id:
                    return row
            raise AssertionError(f'no captured audit row with id={log_id}')

    yield Reader()

    if minted:
        with app.app_context(), Session(db.engine) as session:
            # DESCENDING id, one PK-targeted DELETE each — NOT a single
            # `IN (...)`, which is what this used to be.
            #
            # `source_action_id` is a self-FK, so once a test replays an action the
            # minted set contains both a parent and its child. A single statement
            # gives InnoDB no ordering guarantee and it fails with
            # `1451 Cannot delete or update a parent row`. Descending id is
            # provably safe here: a replay is always inserted after the row it
            # replays, so a child's id always exceeds its parent's — deleting high
            # to low removes every child before its parent, to any chain depth.
            #
            # Still by primary key, per the note above: a range predicate would
            # take an open-ended gap lock and deadlock against concurrent inserts.
            for log_id in sorted(minted, reverse=True):
                session.execute(
                    delete(XrasActionLog).where(
                        XrasActionLog.xras_action_log_id == log_id)
                )
            session.commit()
