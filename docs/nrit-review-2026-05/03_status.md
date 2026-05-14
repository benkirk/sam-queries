# Phase 3 — Status (`src/system_status/`)

> SQLite-bound status DB tier (per-worker tempfile in tests, separate concern from the main `sam` MySQL DB). Alembic-migrated. Quick pass — small subsystem.

## Scope

- `src/system_status/` (models, base, bind routing)
- `migrations/system_status/` (Alembic config + revisions)
- Status routes in `src/webapp/` that consume this data
- Status-related tests in `tests/integration/`
- `scripts/cleanup_status_data.py`, `scripts/ingest_mock_status.py`, `scripts/setup_status_db.py`, `scripts/test_status_db.py`, `scripts/create_status_db.sql`

## Method

1. Understand the SQLAlchemy bind routing (`__bind_key__` per CLAUDE.md), in code and in test fixtures.
2. Walk one ingest path → one read path end-to-end.
3. Alembic posture: are migrations current? Is `stamp head` workflow safe?
4. Data retention / cleanup: what does `cleanup_status_data.py` do, when does it run?
5. Test isolation: `DELETE FROM` pattern in tests — any leakage risk?

## Lenses applied

- Architecture
- Testing
- Operability
- Performance (lightly — small tier)

## Findings

*TBD*

### Architecture

*TBD*

### Testing

*TBD*

### Operability

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*
