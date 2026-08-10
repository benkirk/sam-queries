"""Tests for webapp.utils.config_inspect.schema_drift.

Regression cover for the 2026-08-10 outage: a production DDL change dropped
8 columns the ORM still selected, every page 500'd with MySQL 1054, and
``/api/v1/health/`` reported 200 healthy throughout because a ``SELECT 1``
ping cannot see a column that stopped existing.

Snapshot-dependent (structural) like test_schema_validation.py — "healthy"
here means the committed snapshot genuinely agrees with the ORM.
"""
import pytest

from webapp.utils.config_inspect import (
    reset_schema_drift_cache,
    schema_drift,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_drift_cache():
    """schema_drift memoizes in a module global — never leak across tests."""
    reset_schema_drift_cache()
    yield
    reset_schema_drift_cache()


class TestSchemaDriftClean:
    """The committed test snapshot must agree with the ORM."""

    def test_reports_healthy_against_test_db(self, engine):
        result = schema_drift(engine)
        assert result['status'] == 'healthy', result.get('drift')
        assert 'drift' not in result

    def test_checks_every_mapped_model(self, engine):
        """models_checked reflects the real mapper count, not a sampled few."""
        result = schema_drift(engine)
        assert result['models_checked'] > 50


class TestSchemaDriftDetection:
    """An ORM column the database lacks is the failure this must catch."""

    def test_orm_extra_column_is_unhealthy(self, engine, monkeypatch):
        """Reproduces the outage: ORM selects a column the DB dropped.

        Uses a synthetic name rather than the real `pdb_modified_time` — the
        committed snapshot still carries that column (only production dropped
        it), so it would not simulate drift here.
        """
        import webapp.utils.config_inspect as ci

        tables = ci._mapped_tables()
        tables['users'] = tables['users'] | {'column_dropped_by_a_dba'}
        monkeypatch.setattr(ci, '_mapped_tables', lambda: tables)

        result = schema_drift(engine)

        assert result['status'] == 'unhealthy'
        assert any('users' in d and 'column_dropped_by_a_dba' in d
                   for d in result['drift'])

    def test_db_extra_column_is_ignored(self, engine, monkeypatch):
        """A DB column with no ORM mapping breaks nothing — must stay healthy.

        SQLAlchemy names its columns explicitly, so an unmapped column never
        reaches a query. This is the ``users.deactivate`` case.
        """
        import webapp.utils.config_inspect as ci

        tables = ci._mapped_tables()
        tables['users'] = tables['users'] - {'username'}
        monkeypatch.setattr(ci, '_mapped_tables', lambda: tables)

        assert schema_drift(engine)['status'] == 'healthy'

    def test_missing_table_is_reported_but_not_unhealthy(self, engine, monkeypatch):
        """A table absent from the DB is a different, already-ticketed state.

        A table an environment has not been given yet has its own remedy —
        apply the DDL — and counting it as drift would pin that environment at
        503 and train everyone to ignore this check. It is still *reported*
        under `missing_tables`, which is what let the 2026-08-10 production
        rollout be confirmed: the key went from listing the three XRAS /
        notification tables to being absent entirely.
        """
        import webapp.utils.config_inspect as ci

        tables = ci._mapped_tables()
        tables['table_that_does_not_exist'] = {'id'}
        monkeypatch.setattr(ci, '_mapped_tables', lambda: tables)

        result = schema_drift(engine)

        assert result['status'] == 'healthy'
        assert 'table_that_does_not_exist' in result['missing_tables']

    def test_introspection_failure_is_unknown_not_drift(self):
        """A dead engine must not be misreported as a dropped column."""
        class _DeadEngine:
            def connect(self):
                raise RuntimeError('connection refused')

        result = schema_drift(_DeadEngine())

        assert result['status'] == 'unknown'
        assert 'connection refused' in result['error']
        assert 'drift' not in result


class TestSchemaDriftCaching:
    """One INFORMATION_SCHEMA query per TTL per pod, not per request.

    ``/api/v1/health/`` is public and exempt from rate limiting, so an
    unmemoized introspection query would be an open invitation.
    """

    def test_result_is_memoized(self, engine, monkeypatch):
        import webapp.utils.config_inspect as ci

        calls = []
        real = ci._mapped_tables
        monkeypatch.setattr(ci, '_mapped_tables',
                            lambda: (calls.append(1), real())[1])

        schema_drift(engine)
        schema_drift(engine)
        schema_drift(engine)

        assert len(calls) == 1

    def test_expired_ttl_recomputes(self, engine, monkeypatch):
        import webapp.utils.config_inspect as ci

        calls = []
        real = ci._mapped_tables
        monkeypatch.setattr(ci, '_mapped_tables',
                            lambda: (calls.append(1), real())[1])

        schema_drift(engine, ttl_seconds=0)
        schema_drift(engine, ttl_seconds=0)

        assert len(calls) == 2

    def test_reset_clears_the_memo(self, engine):
        import webapp.utils.config_inspect as ci

        schema_drift(engine)
        assert ci._schema_drift_memo is not None

        reset_schema_drift_cache()
        assert ci._schema_drift_memo is None
