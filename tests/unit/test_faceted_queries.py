"""`querykit.faceted` — the shared count / page / facet trio.

Exercised directly against a throwaway in-memory table rather than through
either real consumer, because the whole point of the package is that it knows
nothing about SAM or system_status. A test that reached for `NotificationLog`
would be testing the notifications wiring, which
`test_notifications_queries.py` already does.

The import-graph class at the bottom is the gate that keeps the package's
central claim — "imports only SQLAlchemy" — true rather than merely written
down. Modelled on `test_notify_import_graph.py`.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from querykit import LogSpec, count_rows, facet_counts, page_rows

SRC = Path(__file__).resolve().parents[2] / 'src'


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    """A miniature log table: an id, an ordering column, two dimensions."""

    __tablename__ = 'querykit_probe'

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))


def _filters(*, min_seq=None, states=None, kinds=None):
    conditions = []
    if min_seq is not None:
        conditions.append(_Row.seq >= min_seq)
    if states:
        conditions.append(_Row.state.in_(list(states)))
    if kinds:
        conditions.append(_Row.kind.in_(list(kinds)))
    return conditions


SPEC = LogSpec(
    model=_Row,
    id_column=_Row.row_id,
    order_columns=(_Row.seq.desc(),),
    dimensions={'state': _Row.state, 'kind': _Row.kind},
    owned_filter={'state': 'states', 'kind': 'kinds'},
    build_filters=_filters,
)

#: (seq, state, kind) — two states x two kinds, deliberately lopsided so a
#: self-exclusion bug cannot pass by symmetry.
_DATA = [
    (1, 'ok', 'alpha'),
    (2, 'ok', 'alpha'),
    (3, 'ok', 'beta'),
    (4, 'bad', 'alpha'),
    (5, 'bad', 'beta'),
]


@pytest.fixture
def probe():
    """A private SQLite session — no bind, no fixture DB, no cleanup."""
    engine = create_engine('sqlite://')
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([_Row(seq=s, state=st, kind=k) for s, st, k in _DATA])
        session.flush()
        yield session


class TestCountAndPage:

    def test_count_honours_every_filter(self, probe):
        assert count_rows(probe, SPEC) == 5
        assert count_rows(probe, SPEC, states=['ok']) == 3
        assert count_rows(probe, SPEC, states=['ok'], kinds=['alpha']) == 2
        assert count_rows(probe, SPEC, min_seq=4) == 2

    def test_rows_come_back_newest_first(self, probe):
        rows = page_rows(probe, SPEC)
        assert [r.seq for r in rows] == [5, 4, 3, 2, 1]

    def test_limit_and_offset_partition_the_result(self, probe):
        first = page_rows(probe, SPEC, limit=2, offset=0)
        second = page_rows(probe, SPEC, limit=2, offset=2)
        assert [r.seq for r in first] == [5, 4]
        assert [r.seq for r in second] == [3, 2]
        assert not {r.row_id for r in first} & {r.row_id for r in second}

    def test_limit_none_means_no_cap(self, probe):
        assert len(page_rows(probe, SPEC, limit=None)) == 5

    def test_the_id_tiebreaker_is_appended(self, probe):
        """Two rows sharing an ordering value must not swap between pages."""
        probe.add_all([_Row(seq=9, state='ok', kind='alpha') for _ in range(4)])
        probe.flush()
        seen = [r.row_id for r in page_rows(probe, SPEC, limit=2, offset=0)]
        seen += [r.row_id for r in page_rows(probe, SPEC, limit=2, offset=2)]
        assert len(set(seen)) == 4, 'a page boundary repeated or dropped a row'

    def test_count_and_page_agree(self, probe):
        filters = {'states': ['ok']}
        assert count_rows(probe, SPEC, **filters) == \
            len(page_rows(probe, SPEC, limit=None, **filters))


class TestFacetsExcludeTheirOwnDimension:
    """The property the whole package exists to implement once."""

    def test_a_dimension_ignores_its_own_filter(self, probe):
        facet = facet_counts(probe, SPEC, 'state', states=['ok'])
        assert facet == {'ok': 3, 'bad': 2}, \
            'picking "ok" must not zero the other state chips'

    def test_a_dimension_still_honours_every_other_filter(self, probe):
        facet = facet_counts(probe, SPEC, 'state', states=['ok'],
                             kinds=['beta'])
        assert facet == {'ok': 1, 'bad': 1}

    def test_the_other_dimension_honours_the_first(self, probe):
        assert facet_counts(probe, SPEC, 'kind', states=['ok']) == \
            {'alpha': 2, 'beta': 1}

    def test_scalar_filters_are_honoured_too(self, probe):
        assert facet_counts(probe, SPEC, 'state', min_seq=4) == \
            {'bad': 2}

    def test_an_unknown_dimension_raises_with_the_vocabulary(self, probe):
        with pytest.raises(ValueError, match='state, kind'):
            facet_counts(probe, SPEC, 'nonesuch')


class TestTheSpecValidatesItself:

    def test_a_dimension_without_an_owned_filter_is_rejected(self):
        """Without the mapping, facet_counts could not self-exclude — and
        would silently return a scoped-to-itself strip instead."""
        with pytest.raises(ValueError, match='owned_filter'):
            LogSpec(model=_Row, id_column=_Row.row_id,
                    order_columns=(_Row.seq.desc(),),
                    dimensions={'state': _Row.state, 'kind': _Row.kind},
                    owned_filter={'state': 'states'},
                    build_filters=_filters)


class TestTheImportGraph:
    """`querykit` imports only SQLAlchemy. Gated, not merely documented.

    `FLASK_ACTIVE` is stripped for the same reason `test_notify_import_graph`
    strips it: pytest sets it in `pytest_configure`, and a child that
    inherited it would make a Flask import legitimate, masking the leak.
    """

    @staticmethod
    def _run(body: str) -> subprocess.CompletedProcess:
        prelude = f'import sys; sys.path.insert(0, {str(SRC)!r})\n'
        env = {k: v for k, v in os.environ.items() if k != 'FLASK_ACTIVE'}
        return subprocess.run(
            [sys.executable, '-c', prelude + textwrap.dedent(body)],
            capture_output=True, text=True, timeout=120, env=env)

    @pytest.mark.parametrize('forbidden', ['flask', 'sam', 'system_status'])
    def test_importing_querykit_pulls_in_nothing_else(self, forbidden):
        result = self._run(f"""
            import querykit
            assert querykit.LogSpec is not None
            print({forbidden!r} in sys.modules)
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'False', (
            f'importing querykit pulled in {forbidden!r} — the package has '
            f'stopped being layer-neutral, and the reason it is a top-level '
            f'peer rather than sam/queries/faceted.py no longer holds')
