"""Centralized display formatting for SAM.

All number, percentage, date, and size formatting should go through these
functions so that output style can be controlled from one place.

Module-level defaults are seeded from SAMConfig / env-vars at import time
and can be overridden once at application / CLI startup via configure().
Each public function also accepts per-call keyword overrides.

Env vars
--------
SAM_RAW_OUTPUT=1    Skip compact notation; emit bare comma-separated integers.
SAM_SIG_FIGS=3      Significant figures for compact number / size display.

Quick reference
---------------
    from sam import fmt

    fmt.number(68_567_808)          # '68.6M'
    fmt.number(68_567_808, raw=True)# '68,567,808'
    fmt.pct(0.4)                    # '0.4%'
    fmt.date_str(some_dt)           # '2024-10-01'
    fmt.size(1_234_567_890_123)     # '1.12 TiB'

    # Jinja2 (call once in create_app)
    fmt.register_jinja_filters(app)
    # → {{ value | fmt_number }}  {{ value | fmt_pct }}  etc.

    # matplotlib
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
"""
import math
import os
from datetime import date, datetime
from typing import Optional, Union
from zoneinfo import ZoneInfo

from config import SAMConfig


# ── Display timezone for naive-UTC datetimes ────────────────────────────────
#
# Database / collector convention is naive-UTC (CLAUDE.md).  When rendering
# datetimes for human eyes, convert to the configured display TZ.  Default
# is America/Denver since the systems and most users are in NCAR's TZ.
# Override with STATUS_DISPLAY_TZ for other deployments.

_DISPLAY_TZ_NAME = os.environ.get('STATUS_DISPLAY_TZ', 'America/Denver')
_DISPLAY_TZ = ZoneInfo(_DISPLAY_TZ_NAME)
_UTC = ZoneInfo('UTC')


def to_local_dt(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a naive-UTC datetime to a tz-aware datetime in the display TZ.

    Returns None unchanged.  `date` (no time) is returned unchanged since
    a calendar date has no time-of-day to localise.  Already-aware
    datetimes are converted to the display TZ; naive datetimes are
    assumed to be UTC (the project-wide convention).
    """
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return dt  # pure date — leave alone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_DISPLAY_TZ)


def local_tz_label() -> str:
    """DST-aware short abbreviation for the active display TZ (e.g. 'MDT'/'MST').
    Falls back to the IANA name if the platform doesn't supply an abbrev."""
    abbr = datetime.now(_DISPLAY_TZ).strftime('%Z')
    return abbr or _DISPLAY_TZ_NAME


def naive_local_to_utc(
    dt: Optional[datetime],
    tz_name: Optional[str] = None,
) -> Optional[datetime]:
    """Treat a naive datetime as wall-clock time in `tz_name` and return the
    equivalent naive-UTC datetime.  Used at form-submit time to normalize
    operator-entered values (browser-local) into the project's naive-UTC
    storage convention.

    None passes through.  An already-aware datetime is converted directly.
    A bad / missing tz_name falls back to STATUS_DISPLAY_TZ."""
    if dt is None:
        return None
    tz = _DISPLAY_TZ
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = _DISPLAY_TZ
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(_UTC).replace(tzinfo=None)

# ── Constants ─────────────────────────────────────────────────────────────────

# Numbers ≤ this are shown exactly with thousands separators ("99,999").
# Numbers > this use compact notation ("100K", "1.23M", …).
# Hard-coded and intentionally obvious — change this one constant to adjust.
COMPACT_THRESHOLD: int = 100_000

# IEC binary size units (default): KiB, MiB, GiB, TiB, PiB
_IEC_UNITS = [
    (2**50, 'PiB'),
    (2**40, 'TiB'),
    (2**30, 'GiB'),
    (2**20, 'MiB'),
    (2**10, 'KiB'),
    (1,     'B'),
]

# SI decimal size units: KB, MB, GB, TB, PB
# Pass size_units='si' to configure() to switch.
_SI_UNITS = [
    (1_000_000_000_000_000, 'PB'),
    (1_000_000_000_000,     'TB'),
    (1_000_000_000,         'GB'),
    (1_000_000,             'MB'),
    (1_000,                 'KB'),
    (1,                     'B'),
]

# ── Module-level config (seeded from env at import time) ──────────────────────

_raw:        bool  = SAMConfig.SAM_RAW_OUTPUT
_sig_figs:   int   = SAMConfig.SAM_SIG_FIGS
_size_units        = _IEC_UNITS  # swap to _SI_UNITS via configure()


def configure(
    *,
    raw:        Optional[bool] = None,
    sig_figs:   Optional[int]  = None,
    size_units: str            = 'iec',
) -> None:
    """Override module defaults at application / CLI startup.

    Args:
        raw:        If True, all functions emit bare numbers instead of compact
                    notation.  Equivalent to SAM_RAW_OUTPUT=1.
        sig_figs:   Significant figures for compact numbers and sizes.
                    Equivalent to SAM_SIG_FIGS=N.
        size_units: 'iec' (default) for TiB/GiB/…; 'si' for TB/GB/….
    """
    global _raw, _sig_figs, _size_units
    if raw is not None:
        _raw = raw
    if sig_figs is not None:
        _sig_figs = sig_figs
    if size_units == 'si':
        _size_units = _SI_UNITS
    elif size_units == 'iec':
        _size_units = _IEC_UNITS


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compact(x: float, sig_figs: int) -> str:
    """Return compact notation for |x| > COMPACT_THRESHOLD.

    Examples (sig_figs=3):
        68_567_808  →  '68.6M'
        18_275_655  →  '18.3M'
         1_234_567  →  '1.23M'
           123_456  →  '123K'
           100_001  →  '100K'
    """
    abs_x = abs(x)
    for threshold, suffix in [
        (1_000_000_000_000, 'T'),
        (1_000_000_000,     'B'),
        (1_000_000,         'M'),
        (1_000,             'K'),
    ]:
        if abs_x >= threshold:
            scaled = x / threshold
            # Digits to the left of the decimal point in scaled value
            mag = math.floor(math.log10(abs(scaled)))
            decimals = max(0, sig_figs - mag - 1)
            return f"{scaled:,.{decimals}f}{suffix}"
    # Should not reach here given COMPACT_THRESHOLD > 1_000, but be safe
    return f"{x:,.0f}"


# ── Public API ────────────────────────────────────────────────────────────────

def round_to_sig_figs(
    x:        Optional[Union[int, float]],
    *,
    sig_figs: Optional[int] = None,
) -> Optional[float]:
    """Round a number to N significant figures (numeric, not string).

    Complements number() and size(), which apply sig-figs to *display*.
    Returns a float suitable for storage or further math.

    Args:
        x:        Value to round.  None → None.
        sig_figs: Significant figures.  Default: SAM_SIG_FIGS.

    Examples (sig_figs=3):
        round_to_sig_figs(458_896_000)  → 459_000_000.0
        round_to_sig_figs(1_500_750)    → 1_500_000.0
        round_to_sig_figs(33_350)       → 33_400.0
        round_to_sig_figs(0)            → 0.0
        round_to_sig_figs(None)         → None
    """
    if x is None:
        return None
    if x == 0:
        return 0.0
    use_sf = _sig_figs if sig_figs is None else sig_figs
    mag = math.floor(math.log10(abs(x)))
    decimals = use_sf - mag - 1
    return round(float(x), decimals)


def number(
    x:        Optional[Union[int, float]],
    *,
    sig_figs: Optional[int]  = None,
    raw:      Optional[bool] = None,
    null:     str            = '—',
) -> str:
    """Format a number.

    Values ≤ COMPACT_THRESHOLD (100,000) are always shown exactly with
    thousands separators ("34,283").  Larger values use compact notation
    ("68.6M", "1.23B") unless raw mode is active.

    Args:
        x:        Value to format.  None → null.
        sig_figs: Significant figures for compact display.  Default: SAM_SIG_FIGS.
        raw:      If True, always emit exact comma-separated integer.
        null:     Placeholder returned for None values.

    Examples:
        number(2)               → '2'
        number(99_999)          → '99,999'
        number(100_001)         → '100K'
        number(68_567_808)      → '68.6M'
        number(68_567_808, raw=True) → '68,567,808'
        number(None)            → '—'
    """
    if x is None:
        return null
    use_raw = _raw      if raw      is None else raw
    use_sf  = _sig_figs if sig_figs is None else sig_figs
    if use_raw or abs(x) <= COMPACT_THRESHOLD:
        return f"{x:,.0f}"
    return _compact(float(x), use_sf)


def pct(
    x:        Optional[Union[int, float]],
    *,
    decimals: int            = 1,
    raw:      Optional[bool] = None,
    null:     str            = '—',
) -> str:
    """Format a percentage value (input already in the 0–100 range).

    Args:
        x:        Percentage value, e.g. 0.4, 75.0, 100.0.
        decimals: Decimal places to display.  Default: 1.
        raw:      If True, emit bare float string without '%' suffix.
        null:     Placeholder returned for None values.

    Examples:
        pct(0.4)          → '0.4%'
        pct(100.0)        → '100.0%'
        pct(33.333, decimals=2) → '33.33%'
        pct(None)         → '—'
    """
    if x is None:
        return null
    use_raw = _raw if raw is None else raw
    if use_raw:
        return str(x)
    return f"{x:.{decimals}f}%"


def date_str(
    d:    Optional[Union[date, datetime]],
    *,
    fmt:  str = '%Y-%m-%d',
    null: str = '—',
) -> str:
    """Format a date or datetime object.

    Args:
        d:    Date or datetime.  None → null.
        fmt:  strftime format string.  Default: ISO date '%Y-%m-%d'.
        null: Placeholder returned for None values.

    Examples:
        date_str(datetime(2024, 10, 1))  → '2024-10-01'
        date_str(None)                   → '—'
        date_str(dt, fmt='%b %Y')        → 'Oct 2024'
    """
    if d is None:
        return null
    return d.strftime(fmt)


def size(
    x_bytes:  Optional[Union[int, float]],
    *,
    sig_figs: Optional[int]  = None,
    raw:      Optional[bool] = None,
    null:     str            = '—',
) -> str:
    """Format a byte count using the active unit system (default: IEC binary).

    Call configure(size_units='si') once at startup to switch to SI (TB/PB/…).

    Args:
        x_bytes:  Size in bytes.  None → null.
        sig_figs: Significant figures.  Default: SAM_SIG_FIGS.
        raw:      If True, emit bare integer byte count.
        null:     Placeholder returned for None values.

    Examples (IEC, sig_figs=3):
        size(512)                → '512 B'
        size(1_536)              → '1.50 KiB'
        size(1_073_741_824)      → '1.00 GiB'
        size(1_234_567_890_123)  → '1.12 TiB'
        size(None)               → '—'
    """
    if x_bytes is None:
        return null
    use_raw = _raw      if raw      is None else raw
    use_sf  = _sig_figs if sig_figs is None else sig_figs
    if use_raw:
        return str(int(x_bytes))
    abs_x = abs(x_bytes)
    for threshold, unit in _size_units:
        if abs_x >= threshold:
            if threshold == 1:
                return f"{int(x_bytes):,} B"
            scaled = x_bytes / threshold
            mag = math.floor(math.log10(abs(scaled))) if scaled != 0 else 0
            decimals = max(0, use_sf - mag - 1)
            return f"{scaled:.{decimals}f} {unit}"
    return f"0 {_size_units[-1][1]}"


# ── Framework integration ─────────────────────────────────────────────────────

def register_jinja_filters(app) -> None:
    """Register fmt_* Jinja2 filters on a Flask application.

    Call once inside create_app() after the app object is created.

    Filters registered:
        fmt_number  — {{ value | fmt_number }}
                      {{ value | fmt_number(sig_figs=4) }}
                      {{ value | fmt_number(raw=true) }}
        fmt_pct     — {{ value | fmt_pct }}
                      {{ value | fmt_pct(decimals=2) }}
        fmt_date    — {{ value | fmt_date }}
                      {{ value | fmt_date(fmt='%b %Y') }}
        fmt_size    — {{ value | fmt_size }}
    """
    app.jinja_env.filters['fmt_number']   = number
    app.jinja_env.filters['fmt_pct']      = pct
    app.jinja_env.filters['fmt_date']     = date_str
    app.jinja_env.filters['fmt_size']     = size
    app.jinja_env.filters['to_local_dt']  = to_local_dt
    # Global (not a filter) so templates can render "{{ local_tz_label() }}"
    # alongside naive-local timestamps that don't go through to_local_dt.
    app.jinja_env.globals['local_tz_label'] = local_tz_label


def mpl_number_formatter(sig_figs: Optional[int] = None):
    """Return a matplotlib FuncFormatter backed by fmt.number().

    Usage:
        import matplotlib.ticker as ticker
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())

    Args:
        sig_figs: Override significant figures for this axis.
    """
    from matplotlib.ticker import FuncFormatter
    sf = sig_figs or _sig_figs
    return FuncFormatter(lambda x, _: number(x, sig_figs=sf))


def mpl_pct_formatter(decimals: int = 0):
    """Return a matplotlib FuncFormatter backed by fmt.pct().

    Input values are in the 0–100 range (not 0–1). Default decimals=0 suits
    tick labels ("25%", "50%"); raise it for tighter axes.

    Usage:
        ax.yaxis.set_major_formatter(fmt.mpl_pct_formatter())
    """
    from matplotlib.ticker import FuncFormatter
    return FuncFormatter(lambda x, _: pct(x, decimals=decimals))
