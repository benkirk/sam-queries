"""Shared helpers for ORM ↔ MySQL schema introspection.

Used by:
  - scripts/check_db_drift.py — one-shot audit against any DB (prod, staging, local)
  - tests/integration/test_schema_validation.py — CI guards run against the test container

Pure read-only INFORMATION_SCHEMA queries plus SQLAlchemy mapper inspection.
No writes, no mocking.
"""
from collections import defaultdict
from typing import Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.schema import Index, UniqueConstraint


# ============================================================================
# Type mapping (extracted from tests/integration/test_schema_validation.py)
# ============================================================================

TYPE_MAPPINGS = {
    'Integer':    ['INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT'],
    'BigInteger': ['BIGINT'],
    'String':     ['VARCHAR', 'CHAR'],
    'Text':       ['TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'TINYTEXT'],
    'Float':      ['FLOAT', 'DOUBLE'],
    'Numeric':    ['DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE'],
    'Boolean':    ['BIT', 'TINYINT'],
    'DateTime':   ['DATETIME', 'TIMESTAMP'],
    'Date':       ['DATE'],
    'TIMESTAMP':  ['TIMESTAMP', 'DATETIME'],
}


def normalize_type(db_type: str) -> str:
    """'VARCHAR(255) UNSIGNED' → 'VARCHAR'. Strips size + sign."""
    base = db_type.split('(')[0].upper()
    base = base.replace(' UNSIGNED', '').replace(' SIGNED', '')
    return base.strip()


def is_acceptable_type_mismatch(mismatch_str: str) -> bool:
    """Known-good MySQL ↔ SQLAlchemy type pairings we don't flag."""
    acceptable = (
        'ORM=String → DB=CHAR',
        'ORM=Integer → DB=TINYINT',
        'ORM=Integer → DB=SMALLINT',
        'ORM=Integer → DB=MEDIUMINT',
    )
    return any(p in mismatch_str for p in acceptable)


# ============================================================================
# Index introspection
# ============================================================================

# DB index records: (name, [columns_in_order], unique:bool)
DBIndex = Tuple[str, Tuple[str, ...], bool]


def get_db_indexes(session, table_name: str) -> List[DBIndex]:
    """Return all indexes on a table from INFORMATION_SCHEMA, EXCLUDING the PRIMARY key.

    Output: [(index_name, (col1, col2, ...), is_unique), ...]
    Columns are in SEQ_IN_INDEX order. PRIMARY KEY is excluded — primary keys are
    validated separately by test_primary_keys_match.
    """
    rows = session.execute(text("""
        SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
          AND INDEX_NAME != 'PRIMARY'
        ORDER BY INDEX_NAME, SEQ_IN_INDEX
    """), {'table_name': table_name}).fetchall()

    grouped: Dict[str, Dict] = defaultdict(lambda: {'cols': [], 'non_unique': None})
    for index_name, col_name, non_unique, _seq in rows:
        grouped[index_name]['cols'].append(col_name)
        # NON_UNIQUE is the same for all rows in an index; capture once
        grouped[index_name]['non_unique'] = int(non_unique)

    return [
        (name, tuple(info['cols']), info['non_unique'] == 0)
        for name, info in sorted(grouped.items())
    ]


def get_orm_indexes(mapper) -> List[DBIndex]:
    """Return all ORM-declared indexes for a mapper, in the same shape as get_db_indexes.

    Picks up:
      - Index(...) entries in __table_args__
      - UniqueConstraint(...) entries in __table_args__ (treated as a unique index)
      - index=True / unique=True on individual columns
    """
    table = mapper.persist_selectable
    out: List[DBIndex] = []

    for idx in table.indexes:
        cols = tuple(c.name for c in idx.columns)
        out.append((idx.name, cols, bool(idx.unique)))

    for cons in table.constraints:
        if isinstance(cons, UniqueConstraint):
            cols = tuple(c.name for c in cons.columns)
            # MySQL treats UniqueConstraint as a UNIQUE INDEX; surface for comparison
            out.append((cons.name or _synthetic_unique_name(table.name, cols), cols, True))

    # De-duplicate: an Index(unique=True) and a UniqueConstraint can both register
    seen = set()
    deduped = []
    for name, cols, uniq in out:
        key = (name, cols, uniq)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, cols, uniq))

    return sorted(deduped)


def _synthetic_unique_name(table: str, cols: Tuple[str, ...]) -> str:
    """Fallback when a UniqueConstraint is unnamed in the ORM."""
    return f"<unnamed unique on {table}({','.join(cols)})>"


def diff_indexes(
    db_indexes: List[DBIndex], orm_indexes: List[DBIndex]
) -> Dict[str, List]:
    """Compare DB and ORM index sets.

    Returns dict with keys:
      - 'in_db_not_orm': indexes present in DB but missing from ORM (← DiskActivity bug)
      - 'in_orm_not_db': indexes declared in ORM but absent from DB
      - 'mismatched':    same name, different columns or uniqueness
    """
    db_by_name = {name: (cols, uniq) for name, cols, uniq in db_indexes}
    orm_by_name = {name: (cols, uniq) for name, cols, uniq in orm_indexes}

    in_db_not_orm = []
    in_orm_not_db = []
    mismatched = []

    for name, (cols, uniq) in db_by_name.items():
        if name not in orm_by_name:
            in_db_not_orm.append((name, cols, uniq))
        elif orm_by_name[name] != (cols, uniq):
            mismatched.append((name, db_by_name[name], orm_by_name[name]))

    for name, (cols, uniq) in orm_by_name.items():
        if name not in db_by_name:
            in_orm_not_db.append((name, cols, uniq))

    return {
        'in_db_not_orm': in_db_not_orm,
        'in_orm_not_db': in_orm_not_db,
        'mismatched': mismatched,
    }


def find_rename_pairs(diff: Dict[str, List]) -> Dict[str, List]:
    """Pair up `in_db_not_orm` and `in_orm_not_db` entries that share
    the same (columns, uniqueness) shape — those are renames, not real
    drift.

    Returns a new dict with:
      - 'renames':       [(cols, uniq, db_name, orm_name), ...]
      - 'in_db_not_orm': filtered (only entries with no rename match)
      - 'in_orm_not_db': filtered (only entries with no rename match)
      - 'mismatched':    unchanged from input

    A rename match is shape-identical: same column tuple, same
    uniqueness flag. This collapses the pairs that prod and ORM
    differ on by name only.
    """
    db_left = list(diff['in_db_not_orm'])
    orm_left = list(diff['in_orm_not_db'])

    # Bucket ORM-side by shape so each DB entry can find its match in O(1)
    orm_by_shape: Dict[Tuple[Tuple[str, ...], bool], List[str]] = defaultdict(list)
    for name, cols, uniq in orm_left:
        orm_by_shape[(cols, uniq)].append(name)

    renames = []
    db_unmatched = []
    consumed_orm_names = set()

    for db_name, cols, uniq in db_left:
        bucket = orm_by_shape.get((cols, uniq))
        if bucket:
            orm_name = bucket.pop(0)
            consumed_orm_names.add(orm_name)
            renames.append((cols, uniq, db_name, orm_name))
        else:
            db_unmatched.append((db_name, cols, uniq))

    orm_unmatched = [
        (name, cols, uniq) for name, cols, uniq in orm_left
        if name not in consumed_orm_names
    ]

    return {
        'renames': renames,
        'in_db_not_orm': db_unmatched,
        'in_orm_not_db': orm_unmatched,
        'mismatched': diff['mismatched'],
    }


# ============================================================================
# Foreign key introspection
# ============================================================================


def get_db_foreign_keys(session, table_name: str) -> Dict[str, Dict]:
    """Return DB-level FK constraints for a table.

    Output: {constraint_name: {
        'columns': [...], 'referenced_table': str, 'referenced_columns': [...],
        'on_delete': str, 'on_update': str
    }}
    """
    rows = session.execute(text("""
        SELECT
            kcu.CONSTRAINT_NAME,
            kcu.COLUMN_NAME,
            kcu.REFERENCED_TABLE_NAME,
            kcu.REFERENCED_COLUMN_NAME,
            kcu.ORDINAL_POSITION,
            rc.DELETE_RULE,
            rc.UPDATE_RULE
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
        JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
          ON kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
         AND kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
        WHERE kcu.TABLE_SCHEMA = DATABASE()
          AND kcu.TABLE_NAME = :table_name
          AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
        ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
    """), {'table_name': table_name}).fetchall()

    fks: Dict[str, Dict] = {}
    for cname, col, rtable, rcol, _ord, on_del, on_upd in rows:
        if cname not in fks:
            fks[cname] = {
                'columns': [],
                'referenced_table': rtable,
                'referenced_columns': [],
                'on_delete': on_del,
                'on_update': on_upd,
            }
        fks[cname]['columns'].append(col)
        fks[cname]['referenced_columns'].append(rcol)
    return fks


# ============================================================================
# Column introspection (mirror of test_schema_validation.py helpers)
# ============================================================================


def get_db_columns(session, table_name: str) -> Dict[str, Dict]:
    """Return {column_name: {'type', 'nullable', 'key', 'extra', 'default'}}."""
    result = session.execute(text("""
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
    """), {'table_name': table_name})
    return {
        row[0]: {
            'type':     row[1],
            'nullable': row[2] == 'YES',
            'key':      row[3],
            'extra':    row[4],
            'default':  row[5],
        }
        for row in result
    }


def get_table_type(session, table_name: str):
    """Return 'BASE TABLE' | 'VIEW' | None."""
    result = session.execute(text("""
        SELECT TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
    """), {'table_name': table_name})
    row = result.fetchone()
    return row[0] if row else None


# ============================================================================
# ORM table iteration
# ============================================================================


def iter_table_mappers(base_registry):
    """Yield mappers backed by a real base table on the default bind.

    Excludes views (via info={'is_view': True}) and cross-bind models
    (e.g. system_status SQLite tables via __bind_key__). Mirrors the
    filter in test_schema_validation.py:113-123.
    """
    for mapper in base_registry.mappers:
        if mapper.persist_selectable.info.get('is_view', False):
            continue
        cls = mapper.class_
        if hasattr(cls, '__bind_key__') and cls.__bind_key__ is not None:
            continue
        yield mapper
