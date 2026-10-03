#!/usr/bin/env python3
"""Count SAM's tables three ways and write data/table_counts.tsv.

Live: information_schema on the obfuscated test DB (host port 3307, never 3306).
ORM: the SAM models' metadata (views carry info['is_view']). Also system_status's ORM.
Run by refresh_data.sh with the sam-queries Python; SAMUEL_REPO locates the models.
Unmapped table names go to stderr only: legacy scratch tables can carry people's names.
"""
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

repo = Path(os.environ.get('SAMUEL_REPO', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(repo / 'src'))

url = make_url(os.environ.get('SAM_TEST_DB_URL', 'mysql+pymysql://root:root@127.0.0.1:3307/sam'))
if url.host not in ('127.0.0.1', 'localhost') or url.port != 3307:
    sys.exit(f'count_tables: refusing {url.host}:{url.port}; only the obfuscated test DB (3307)')

with create_engine(url).connect() as conn:
    live = dict(conn.execute(text(
        'SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = :s'),
        {'s': url.database}).all())

import sam  # noqa: E402,F401  (registers every model)
from sam.base import Base  # noqa: E402
import system_status  # noqa: E402,F401  (registers its models)
from system_status.base import StatusBase  # noqa: E402

orm = Base.metadata.tables
orm_views = {n for n, t in orm.items() if t.info.get('is_view')}
live_views = {n for n, kind in live.items() if kind == 'VIEW'}
unmapped = sorted(set(live) - set(orm))

rows = [
    ('sam_db_tables', len(live) - len(live_views), 'information_schema BASE TABLE, test DB'),
    ('sam_db_views', len(live_views), 'information_schema VIEW, test DB'),
    ('sam_orm_tables', len(orm) - len(orm_views), 'SAM models, excluding views'),
    ('sam_orm_views', len(orm_views), "SAM models with info['is_view']"),
    ('sam_unmapped', len(unmapped), 'in the DB, no model (names on stderr)'),
    ('sam_orm_missing', len(set(orm) - set(live)), 'modeled, absent from the DB'),
    ('status_orm_tables', len(StatusBase.metadata.tables), 'system_status models'),
]
out = Path(__file__).resolve().parent / 'data'
(out / 'table_counts.tsv').write_text(
    'what\tcount\tmethod\n' + ''.join(f'{w}\t{n}\t{m}\n' for w, n, m in rows))
print('unmapped:', ' '.join(unmapped), file=sys.stderr)
print((out / 'table_counts.tsv').read_text(), end='')
