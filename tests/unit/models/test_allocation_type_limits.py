"""``AllocationTypeResourceLimit``: the request bounds per type per resource (XRAS_SUBMISSION.md § 4.5).

Rows are built on pairs the seed never names (Education x Derecho), so these hold whether or
not the test database carries the seed.
"""

import re
from _paths import REPO_ROOT

import pytest

from sam.accounting.allocation_type_limits import (
    AllocationTypeResourceLimit,
    limits_for_allocation_type,
    lookup_resource_limit,
)
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource


def _ids(session, type_name, resource_name):
    types = [t.allocation_type_id for t in session.query(AllocationType)
             .filter(AllocationType.allocation_type == type_name)
             .order_by(AllocationType.allocation_type_id)]
    resource = session.query(Resource).filter(Resource.resource_name == resource_name).one()
    return types, resource.resource_id


@pytest.fixture
def education_derecho(session):
    types, resource_id = _ids(session, 'Education', 'Derecho')
    if not types:
        pytest.skip('no Education allocation type in this snapshot')
    return types, resource_id


class TestBounds:

    @pytest.mark.parametrize('bounds', [dict(min_amount=10, default_amount=5),
                                        dict(default_amount=50, max_amount=10),
                                        dict(min_amount=10, max_amount=5),
                                        dict(max_amount=-1)])
    def test_out_of_order_bounds_are_refused(self, session, education_derecho, bounds):
        types, resource_id = education_derecho
        with pytest.raises(ValueError):
            AllocationTypeResourceLimit.create(session, allocation_type_id=types[0],
                                               resource_id=resource_id, created_by='t',
                                               **bounds)

    def test_update_merges_and_revalidates(self, session, education_derecho):
        types, resource_id = education_derecho
        row = AllocationTypeResourceLimit.create(
            session, allocation_type_id=types[0], resource_id=resource_id,
            created_by='t', default_amount=100, max_amount=1000)
        row.update(max_amount=None)
        assert (row.default_amount, row.max_amount) == (100, None)
        with pytest.raises(ValueError):
            row.update(min_amount=500)
        row.update(active=False)
        assert not row.is_active

    def test_a_second_row_for_a_pair_is_refused(self, session, education_derecho):
        types, resource_id = education_derecho
        AllocationTypeResourceLimit.create(session, allocation_type_id=types[0],
                                           resource_id=resource_id, created_by='t')
        with pytest.raises(ValueError, match='already have a limit'):
            AllocationTypeResourceLimit.create(session, allocation_type_id=types[0],
                                               resource_id=resource_id, created_by='t')

    def test_the_joins_resolve_without_foreign_keys(self, session, education_derecho):
        types, resource_id = education_derecho
        row = AllocationTypeResourceLimit.create(session, allocation_type_id=types[0],
                                                 resource_id=resource_id, created_by='t')
        assert (row.allocation_type.allocation_type, row.resource.resource_name) == \
            ('Education', 'Derecho')

    def test_refusal_names_the_bound(self, session, education_derecho):
        types, resource_id = education_derecho
        row = AllocationTypeResourceLimit.create(
            session, allocation_type_id=types[0], resource_id=resource_id,
            created_by='t', min_amount=10, max_amount=1000)
        assert row.refusal(500) is None
        assert '1,000 limit' in row.refusal(1001)
        assert '10 minimum' in row.refusal(5)


class TestLookupByName:

    def test_every_row_of_a_duplicated_name_maps(self, session, education_derecho):
        types, resource_id = education_derecho
        row = AllocationTypeResourceLimit.create(
            session, allocation_type_id=types[-1], resource_id=resource_id,
            created_by='t', max_amount=42)
        assert lookup_resource_limit(session, allocation_type_name='Education',
                                     resource_name='Derecho') is row
        assert limits_for_allocation_type(session, 'Education')['Derecho'] is row

    def test_an_unconfigured_or_inactive_pair_has_no_limit(self, session, education_derecho):
        types, resource_id = education_derecho
        assert lookup_resource_limit(session, allocation_type_name='No Such Type',
                                     resource_name='Derecho') is None
        row = AllocationTypeResourceLimit.create(
            session, allocation_type_id=types[0], resource_id=resource_id,
            created_by='t', max_amount=42)
        row.update(active=False)
        assert lookup_resource_limit(session, allocation_type_name='Education',
                                     resource_name='Derecho') is None


_SEED_ROW = re.compile(r"SELECT\s+'([^']+)'\s*,\s*'([^']+)'\s*,\s*(\S+?)\s*,\s*(\S+)$", re.M)


def _seed_rows(name):
    return _SEED_ROW.findall((REPO_ROOT / 'scripts' / 'sql' / name).read_text())


class TestTheSeed:
    """The two dialects seed the same rows, and those rows obey the bounds rule."""

    def test_both_dialects_carry_the_same_rows(self):
        mysql = _seed_rows('seed_allocation_type_resource_limit.sql')
        assert len(mysql) == 33
        assert mysql == _seed_rows('seed_allocation_type_resource_limit_pg.sql')

    def test_every_seeded_default_sits_under_its_ceiling(self):
        for type_name, resource, default, ceiling in _seed_rows(
                'seed_allocation_type_resource_limit.sql'):
            if default != 'NULL':
                assert float(default) <= float(ceiling), (type_name, resource)

    def test_the_panels_get_no_default(self):
        rows = _seed_rows('seed_allocation_type_resource_limit.sql')
        assert {d for t, _, d, _ in rows if t in ('CHAP', 'NSC')} == {'NULL'}
