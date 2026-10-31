"""Shared pieces for tests whose XRAS writes commit for real: the audit tables and the
submission rows are written on sessions of their own, outside the per-test SAVEPOINT."""

from contextlib import contextmanager


@contextmanager
def tracking_factory(engine, *models):
    """A session factory; every *models* row its sessions flush is deleted by id at exit."""
    from sqlalchemy import delete, event, inspect
    from sqlalchemy.orm import Session

    pks = {m: inspect(m).primary_key[0] for m in models}
    created = {m: [] for m in models}

    def make():
        session = Session(engine)

        @event.listens_for(session, 'after_flush')
        def _track(sess, _ctx):
            for o in sess.new:
                for m in models:
                    if isinstance(o, m):
                        created[m].append(getattr(o, pks[m].key))
        return session

    try:
        yield make
    finally:
        with engine.begin() as conn:
            for m in models:
                if created[m]:
                    conn.execute(delete(m).where(pks[m].in_(created[m])))


def write_result(operation, *, method='POST', verified=True, **kw):
    """An ``XrasWriteResult`` as the admin client reports a 200, verified unless said otherwise."""
    from sam.integration.xras_api.admin_client import XrasWriteResult
    return XrasWriteResult(operation=operation, method=method, path='/p', http_status=200,
                           verified=verified, verify_detail='detail', **kw)
