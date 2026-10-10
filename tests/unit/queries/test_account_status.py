"""The legacy account-status calculator shared by fstree and HEUV."""

from datetime import datetime

import pytest

from sam.queries import account_status as st

START, END = datetime(2026, 10, 1), datetime(2027, 9, 30, 23, 59, 59)


@pytest.mark.parametrize('value, expected', [(2.5, 3), (-0.5, 0), (-2.5, -2), (0.49, 0), (3147.5, 3148)])
def test_java_round_is_half_up(value, expected):
    assert st.java_round(value) == expected


def test_divisors_differ_by_one_day():
    assert st.legacy_divisor(START, END) == 364
    assert st.fstree_divisor(START, END, now=START) == 363
    assert st.java_round(st.threshold_allocation(30, 25_000_000, st.legacy_divisor(START, END))) == 2_060_440


def test_one_day_allocation_has_zero_legacy_divisor():
    assert st.legacy_divisor(START, START.replace(hour=23)) == 0


@pytest.mark.parametrize('usage, thresholds, windows, expected', [
    (10, (None, None), (0, 0), st.NORMAL),
    (101, (None, None), (0, 0), st.OVERSPENT),
    (10, (50, None), (60, 0), st.EXCEED_ONE),
    (10, (50, 50), (60, 200), st.EXCEED_TWO),
    (10, (50, 50), (1, 1), st.NORMAL),
])
def test_charge_status(usage, thresholds, windows, expected):
    # amount 100 over divisor 30: at 50% the 30-day limit is 50, the 90-day limit 150.
    assert st.charge_status(usage, 100, thresholds, windows, divisor=30) == expected


def test_charge_status_without_amount_or_divisor():
    assert st.charge_status(5, None, (1, 1), (99, 99), divisor=30) == st.NORMAL
    assert st.charge_status(5, 100, (1, 1), (99, 99), divisor=None) == st.NORMAL


@pytest.mark.parametrize('kw, expected', [
    (dict(has_account=False, has_active=True, has_future=True, has_prior=True, project_active=False), st.NO_ACCOUNT),
    (dict(has_account=True, has_active=False, has_future=True, has_prior=True, project_active=True), st.WAITING),
    (dict(has_account=True, has_active=False, has_future=False, has_prior=True, project_active=True), st.EXPIRED),
    (dict(has_account=True, has_active=False, has_future=False, has_prior=False, project_active=True), st.NO_ALLOCATION),
    (dict(has_account=True, has_active=True, has_future=False, has_prior=False, project_active=False), st.DISABLED),
    (dict(has_account=True, has_active=True, has_future=False, has_prior=False, project_active=True), None),
])
def test_lifecycle_precedence(kw, expected):
    assert st.lifecycle_status(**kw) == expected
