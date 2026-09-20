"""The DB server clock must match the app clock (SAM is naive-Mountain).

`sam_now()` and every `CURRENT_TIMESTAMP` / `onupdate` column resolve in the
database server's timezone, and the app stamps naive datetimes that raw SQL
compares against that clock (`Model.is_active`, allocation/queue/machine date
ranges, ...). If the DB container or server runs a different zone than the app,
every such comparison is silently off by the offset — the exact bite of a UTC
dev container against a naive-Mountain app. This guard fails loudly when they
diverge, in dev and in CI, on either backend.
"""
from datetime import datetime

from sqlalchemy import select

from sam.sqlcompat import sam_now


def test_db_clock_matches_app_clock(session):
    db_now = session.execute(select(sam_now())).scalar()
    skew = abs((db_now - datetime.now()).total_seconds())
    assert skew < 300, (
        f'DB clock {db_now} is {skew:.0f}s from the app clock — the database '
        f'server timezone does not match the app (SAM is naive-Mountain). Set '
        f'TZ=America/Denver on the DB container/server (see compose.yaml mysql).')
