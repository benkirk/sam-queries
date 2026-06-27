"""Unit tests for sam.enums — pure Python, no DB."""

import pytest

from sam.enums import ResourceTypeName


class TestAllocationUnit:
    """ResourceTypeName.allocation_unit — the label shown next to a project
    card's '<n> allocated' figure."""

    @pytest.mark.parametrize('rtype, expected', [
        ('HPC', 'hours'),
        ('DAV', 'hours'),
        ('DISK', 'TiB'),
        ('ARCHIVE', 'TiB'),
        ('DATA ACCESS', None),
        ('something-unknown', None),
    ])
    def test_unit_by_type(self, rtype, expected):
        assert ResourceTypeName.allocation_unit(rtype) == expected

    @pytest.mark.parametrize('rtype', ['HPC', 'DAV', 'DISK', 'ARCHIVE'])
    def test_access_boolean_amount_one_is_unitless(self, rtype):
        # allocated == 1 is an access-boolean grant (Gust, HPC_Futures_Lab,
        # Quasar, ...) — never labelled, regardless of type.
        assert ResourceTypeName.allocation_unit(rtype, 1) is None

    def test_real_amount_keeps_unit(self):
        assert ResourceTypeName.allocation_unit('HPC', 1_500_000) == 'hours'
        assert ResourceTypeName.allocation_unit('DISK', 5_000) == 'TiB'

    def test_enum_member_arg_matches_string(self):
        # StrEnum members compare equal to their string values, so passing the
        # member works the same as passing the bare DB string.
        assert ResourceTypeName.allocation_unit(ResourceTypeName.HPC) == 'hours'
