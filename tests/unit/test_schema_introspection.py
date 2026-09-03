"""Pure-function guards for the shared schema-introspection library.

These pin the two fixes for the false-positive "type mismatch" noise the
`orm_inventory.py` audit reported against prod (all on hand-written utf8mb4
XRAS/notification tables, where the ORM was actually correct):

  1. `SmallInteger` was missing from `TYPE_MAPPINGS`, so `xras_action_log`'s
     deliberate `SmallInteger` (matching `SMALLINT UNSIGNED`) was flagged.
  2. `normalize_type` didn't strip the `COLLATE "..."` suffix that SQLAlchemy's
     reflected type string carries for non-default-collation columns, so every
     utf8mb4 `Text` column read `TEXT COLLATE "utf8mb4_general_ci"` and missed
     the `Text` allowlist.

DB-free — no container, no reflection.
"""

from __future__ import annotations

import pytest

from scripts.lib.schema_introspection import (
    TYPE_MAPPINGS,
    normalize_type,
)

pytestmark = pytest.mark.unit


class TestNormalizeType:
    def test_strips_collate_suffix(self):
        assert normalize_type('TEXT COLLATE "utf8mb4_general_ci"') == 'TEXT'

    def test_strips_character_set_and_size(self):
        assert normalize_type('varchar(128) CHARACTER SET utf8mb4') == 'VARCHAR'

    def test_still_strips_size_and_sign(self):
        assert normalize_type('smallint unsigned') == 'SMALLINT'
        assert normalize_type('VARCHAR(255) UNSIGNED') == 'VARCHAR'


class TestTypeMappings:
    def test_small_integer_maps_to_smallint(self):
        assert 'SMALLINT' in TYPE_MAPPINGS['SmallInteger']

    @pytest.mark.parametrize('orm_type, db_raw', [
        ('SmallInteger', 'smallint unsigned'),
        ('Text', 'TEXT COLLATE "utf8mb4_general_ci"'),
    ])
    def test_reflected_pairings_resolve_as_acceptable(self, orm_type, db_raw):
        # The membership test the audit tools apply, exercised end-to-end.
        assert normalize_type(db_raw) in TYPE_MAPPINGS[orm_type]
