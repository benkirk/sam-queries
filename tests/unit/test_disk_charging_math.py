"""Unit tests for the TiB-year storage charging formula.

Pin the formula and the constants. The headline change vs legacy:

  TiB-years = (bytes * reporting_interval) / 365 / 1024**4

Legacy was the same shape but used 10**12 (decimal TB) — keep these
verifications tight so a future "round to 365.25" or "drop to 1000**4"
regression is caught immediately.
"""

import pytest

from sam.summaries.disk_summaries import (
    BYTES_PER_TIB,
    DAYS_IN_YEAR,
    DISK_CHARGING_TIB_EPOCH,
    tib_years,
)


def test_constants_pin_units():
    assert BYTES_PER_TIB == 1024 ** 4
    assert DAYS_IN_YEAR == 365


def test_epoch_is_immutable_date():
    # The plan committed 2026-04-18 as the cutover. Lock it in — a
    # change here must be deliberate and accompanied by a documented
    # plan update, not a drive-by.
    from datetime import date
    assert DISK_CHARGING_TIB_EPOCH == date(2026, 1, 3)


def test_wchapman_naml0001_worked_example():
    """Match the verification target in DISK_CHARGING.md.

    From acct.glade.2026-04-18 row:
        col6 (KiB) = 235,938,728,304
        bytes      = 235,938,728,304 × 1024 = 241,601,257,783,296
        ty (TiB)   = 241,601,257,783,296 × 7 / 365 / 1024**4
                   ≈ 4.21500 (legacy decimal-TB-yr was 4.63345)
    """
    bytes_ = 241_601_257_783_296
    ty = tib_years(bytes_, 7)
    # Expected to 4 decimals; computed as bytes×7/365/1024⁴.
    expected = bytes_ * 7 / 365 / (1024 ** 4)
    assert ty == pytest.approx(expected, rel=1e-12)
    # Also pin a hand-checked value to four decimals — guards against
    # silently swapping constants.
    assert ty == pytest.approx(4.2141, abs=1e-4)


def test_zero_bytes_zero_charge():
    assert tib_years(0, 7) == 0.0


def test_zero_interval_zero_charge():
    assert tib_years(10 ** 12, 0) == 0.0


def test_tib_year_definition_constant_storage_for_one_year():
    # Holding 1 TiB constant for 365 days = 1 TiB-year.
    # Single weekly snapshot: bytes = 1 TiB, interval = 7.
    # Cumulative billing of 52 such snapshots ≈ 52 * 7 / 365 = 0.997.
    bytes_ = BYTES_PER_TIB
    weekly = tib_years(bytes_, 7)
    assert weekly == pytest.approx(7 / 365, abs=1e-9)
    # 52 weeks comes within rounding of 1 TiB-year:
    assert 52 * weekly == pytest.approx(52 * 7 / 365, abs=1e-9)


def test_units_match_allocation_amount_pattern():
    # Allocation.amount for disk is in TiB. 100 TiB constant for 1 year
    # → 100 TiB-years should match a hand calculation.
    bytes_ = 100 * BYTES_PER_TIB
    # 365 daily snapshots of interval=1 each (the "if cadence changes"
    # case from the legacy doc):
    daily_total = 365 * tib_years(bytes_, 1)
    assert daily_total == pytest.approx(100.0, abs=1e-6)
    # 52 weekly snapshots with interval=7:
    weekly_total = 52 * tib_years(bytes_, 7)
    assert weekly_total == pytest.approx(52 * 7 / 365 * 100.0, abs=1e-6)
