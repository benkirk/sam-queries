"""Unit tests for sam.fmt — centralized display formatting.

Ported verbatim from tests/unit/test_fmt.py — pure Python, no DB, no
mocks. The autouse `reset_fmt_config` fixture restores module-level
defaults around each test so ordering is irrelevant.
"""
from datetime import date, datetime

import pytest

import sam.fmt as fmt  # noqa: F401 — kept for parity with legacy module import
from sam.fmt import (
    COMPACT_THRESHOLD,
    configure,
    date_str,
    naive_local_to_utc,
    number,
    pct,
    round_to_sig_figs,
    size,
    to_local_dt,
)


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fmt_config():
    configure(raw=False, sig_figs=3, size_units='iec')
    yield
    configure(raw=False, sig_figs=3, size_units='iec')


# ============================================================================
# number()
# ============================================================================


class TestNumber:

    def test_zero(self):
        assert number(0) == '0'

    def test_small_integers_exact(self):
        assert number(1) == '1'
        assert number(99) == '99'
        assert number(2) == '2'

    def test_below_threshold_exact_with_commas(self):
        assert number(1_000) == '1,000'
        assert number(99_999) == '99,999'

    def test_at_threshold_exact(self):
        assert number(COMPACT_THRESHOLD) == f"{COMPACT_THRESHOLD:,}"

    def test_just_above_threshold_compact(self):
        assert number(100_001) == '100K'

    def test_thousands(self):
        assert number(123_456) == '123K'
        assert number(999_999) == '1,000K'

    def test_millions_3sig(self):
        assert number(1_000_000) == '1.00M'
        assert number(1_234_567) == '1.23M'
        assert number(18_275_655) == '18.3M'
        assert number(68_567_808) == '68.6M'
        assert number(100_000_000) == '100M'

    def test_billions(self):
        assert number(1_234_567_890) == '1.23B'

    def test_trillions(self):
        assert number(1_234_567_890_123) == '1.23T'

    def test_negative(self):
        assert number(-68_567_808) == '-68.6M'
        assert number(-500) == '-500'

    def test_float_input(self):
        assert number(68_567_808.0) == '68.6M'

    def test_none_returns_null(self):
        assert number(None) == '—'
        assert number(None, null='N/A') == 'N/A'

    def test_raw_override_per_call(self):
        assert number(68_567_808, raw=True) == '68,567,808'
        assert number(1_234_567, raw=True) == '1,234,567'

    def test_raw_below_threshold_unchanged(self):
        assert number(1_000, raw=True) == '1,000'

    def test_sig_figs_override(self):
        assert number(68_567_808, sig_figs=4) == '68.57M'
        assert number(68_567_808, sig_figs=2) == '69M'
        assert number(1_234_567, sig_figs=2) == '1.2M'

    def test_configure_raw_global(self):
        configure(raw=True)
        assert number(68_567_808) == '68,567,808'

    def test_configure_sig_figs_global(self):
        configure(sig_figs=2)
        assert number(68_567_808) == '69M'
        assert number(18_275_655) == '18M'

    def test_per_call_overrides_global(self):
        configure(raw=True)
        assert number(68_567_808, raw=False) == '68.6M'


# ============================================================================
# round_to_sig_figs()
# ============================================================================


class TestRoundToSigFigs:

    def test_large_value_plan_example(self):
        # 688M × 0.667 = 458,896,000 → 459,000,000 at 3 sig figs
        assert round_to_sig_figs(458_896_000) == 459_000_000.0

    def test_million_scale(self):
        # 2.25M × 0.667 = 1,500,750 → 1,500,000 at 3 sig figs
        assert round_to_sig_figs(1_500_750) == 1_500_000.0

    def test_rounds_up(self):
        # 33,350 → 33,400 at 3 sig figs (banker's rounding: 3.335 → 3.34? no,
        # Python round() is banker's for .5 exact, but 33_350 in IEEE float
        # is exact; at 3 sig figs we round the last kept digit)
        assert round_to_sig_figs(33_350) == 33_400.0

    def test_zero(self):
        assert round_to_sig_figs(0) == 0.0
        assert round_to_sig_figs(0.0) == 0.0

    def test_none_returns_none(self):
        assert round_to_sig_figs(None) is None

    def test_small_integer_unchanged(self):
        # Small integers already within 3 sig figs survive unchanged.
        assert round_to_sig_figs(5) == 5.0
        assert round_to_sig_figs(42) == 42.0
        assert round_to_sig_figs(100) == 100.0

    def test_negative(self):
        assert round_to_sig_figs(-458_896_000) == -459_000_000.0
        assert round_to_sig_figs(-1_500_750) == -1_500_000.0

    def test_sig_figs_override(self):
        assert round_to_sig_figs(458_896_000, sig_figs=2) == 460_000_000.0
        assert round_to_sig_figs(458_896_000, sig_figs=4) == 458_900_000.0

    def test_configure_sig_figs_global(self):
        configure(sig_figs=2)
        assert round_to_sig_figs(458_896_000) == 460_000_000.0

    def test_returns_float(self):
        assert isinstance(round_to_sig_figs(1_000), float)
        assert isinstance(round_to_sig_figs(0), float)


# ============================================================================
# pct()
# ============================================================================


class TestPct:

    def test_typical(self):
        assert pct(0.4) == '0.4%'
        assert pct(75.0) == '75.0%'
        assert pct(100.0) == '100.0%'

    def test_zero(self):
        assert pct(0) == '0.0%'
        assert pct(0.0) == '0.0%'

    def test_decimals_override(self):
        assert pct(33.333, decimals=2) == '33.33%'
        assert pct(0.4, decimals=0) == '0%'

    def test_none_returns_null(self):
        assert pct(None) == '—'
        assert pct(None, null='N/A') == 'N/A'

    def test_raw_returns_bare_string(self):
        assert pct(0.4, raw=True) == '0.4'
        assert pct(100.0, raw=True) == '100.0'

    def test_configure_raw_global(self):
        configure(raw=True)
        assert pct(0.4) == '0.4'


# ============================================================================
# date_str()
# ============================================================================


class TestDateStr:

    def test_datetime(self):
        assert date_str(datetime(2024, 10, 1, 12, 30)) == '2024-10-01'

    def test_date(self):
        assert date_str(date(2026, 9, 30)) == '2026-09-30'

    def test_none_returns_null(self):
        assert date_str(None) == '—'
        assert date_str(None, null='N/A') == 'N/A'

    def test_custom_format(self):
        assert date_str(datetime(2024, 10, 1), fmt='%b %Y') == 'Oct 2024'
        assert date_str(datetime(2024, 10, 1), fmt='%Y-%m-%d %H:%M') == '2024-10-01 00:00'

    def test_not_affected_by_raw(self):
        configure(raw=True)
        assert date_str(date(2024, 10, 1)) == '2024-10-01'


# ============================================================================
# size()
# ============================================================================


class TestSize:

    def test_bytes(self):
        assert size(0) == '0 B'
        assert size(512) == '512 B'
        assert size(1_023) == '1,023 B'

    def test_kib(self):
        assert size(1_024) == '1.00 KiB'
        assert size(1_536) == '1.50 KiB'

    def test_mib(self):
        assert size(1_048_576) == '1.00 MiB'
        assert size(10_485_760) == '10.0 MiB'

    def test_gib(self):
        assert size(1_073_741_824) == '1.00 GiB'

    def test_tib(self):
        assert size(1_099_511_627_776) == '1.00 TiB'
        assert size(1_234_567_890_123) == '1.12 TiB'

    def test_pib(self):
        pib = 2**50
        assert size(pib) == '1.00 PiB'

    def test_none_returns_null(self):
        assert size(None) == '—'
        assert size(None, null='N/A') == 'N/A'

    def test_raw_returns_integer_bytes(self):
        assert size(1_099_511_627_776, raw=True) == '1099511627776'

    def test_configure_raw_global(self):
        configure(raw=True)
        assert size(1_073_741_824) == '1073741824'

    def test_sig_figs_override(self):
        assert size(1_234_567_890_123, sig_figs=4) == '1.123 TiB'
        assert size(1_234_567_890_123, sig_figs=2) == '1.1 TiB'

    def test_si_units_via_configure(self):
        configure(size_units='si')
        assert size(1_000_000_000_000) == '1.00 TB'
        assert size(1_000_000) == '1.00 MB'

    def test_iec_restored_after_si(self):
        configure(size_units='si')
        configure(size_units='iec')
        assert size(1_099_511_627_776) == '1.00 TiB'


# ============================================================================
# configure()
# ============================================================================


class TestConfigure:

    def test_sig_figs_persists(self):
        configure(sig_figs=5)
        assert number(68_567_808) == '68.568M'

    def test_raw_persists(self):
        configure(raw=True)
        assert number(68_567_808) == '68,567,808'
        assert pct(0.4) == '0.4'

    def test_reset_restores_defaults(self):
        configure(raw=True, sig_figs=5)
        configure(raw=False, sig_figs=3)
        assert number(68_567_808) == '68.6M'
        assert pct(0.4) == '0.4%'


# ============================================================================
# Timezone helpers — to_local_dt / naive_local_to_utc
#
# Form values arrive naive in the operator's browser TZ; storage is naive-UTC;
# display shifts back to the configured display TZ.  These tests pin the
# round-trip math against real IANA zones so DST-window changes don't bit-rot
# silently.
# ============================================================================


class TestNaiveLocalToUTC:
    """Operator-entered naive datetimes → naive-UTC for storage."""

    def test_none_passes_through(self):
        assert naive_local_to_utc(None) is None

    def test_browser_eastern_summer_to_utc(self):
        # 2026-07-15 14:30 EDT (UTC-4) → 18:30 UTC
        dt = datetime(2026, 7, 15, 14, 30)
        utc = naive_local_to_utc(dt, 'America/New_York')
        assert utc == datetime(2026, 7, 15, 18, 30)
        assert utc.tzinfo is None

    def test_browser_mountain_summer_to_utc(self):
        # 2026-07-15 14:30 MDT (UTC-6) → 20:30 UTC
        dt = datetime(2026, 7, 15, 14, 30)
        utc = naive_local_to_utc(dt, 'America/Denver')
        assert utc == datetime(2026, 7, 15, 20, 30)

    def test_browser_mountain_winter_to_utc(self):
        # 2026-01-15 14:30 MST (UTC-7) → 21:30 UTC — DST off
        dt = datetime(2026, 1, 15, 14, 30)
        utc = naive_local_to_utc(dt, 'America/Denver')
        assert utc == datetime(2026, 1, 15, 21, 30)

    def test_missing_tz_falls_back_to_display(self):
        # No tz_name → uses STATUS_DISPLAY_TZ default (America/Denver).
        dt = datetime(2026, 7, 15, 14, 30)
        assert naive_local_to_utc(dt) == naive_local_to_utc(dt, 'America/Denver')

    def test_invalid_tz_falls_back_to_display(self):
        dt = datetime(2026, 7, 15, 14, 30)
        assert naive_local_to_utc(dt, 'Not/A_Zone') == naive_local_to_utc(dt, 'America/Denver')


class TestRoundTrip:
    """An operator-entered datetime stored and re-displayed must come back
    looking like what they typed (in their TZ)."""

    def test_eastern_round_trip(self):
        operator_typed = datetime(2026, 7, 15, 14, 30)  # 2:30 PM EDT
        stored_utc = naive_local_to_utc(operator_typed, 'America/New_York')
        # …and to_local_dt converts back to the *display* TZ (Mountain).
        # Different operator viewing in Mountain sees 12:30 PM MDT.
        viewed = to_local_dt(stored_utc)
        assert viewed.hour == 12
        assert viewed.minute == 30
        # 14:30 EDT == 18:30 UTC == 12:30 MDT — sanity.

    def test_mountain_round_trip_idempotent(self):
        # When operator and viewer are both in Mountain (the default),
        # stored value displays back exactly as typed.
        operator_typed = datetime(2026, 7, 15, 14, 30)
        stored_utc = naive_local_to_utc(operator_typed, 'America/Denver')
        viewed = to_local_dt(stored_utc)
        assert viewed.hour == 14
        assert viewed.minute == 30
