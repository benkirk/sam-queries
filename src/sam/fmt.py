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
from datetime import date, datetime, timedelta
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


def hours(
    seconds:  Optional[Union[int, float]],
    *,
    decimals: int  = 2,
    null:     str  = '—',
) -> str:
    """Format a duration given in seconds as a decimal-hours string.

    Useful for elapsed/walltime columns where the underlying integer
    second count is awkward (a 77-second test job and a 23-hour
    production run shouldn't both display as the same magnitude).
    Defaults to 2 decimal places so sub-minute jobs still render as
    ``0.02`` rather than collapsing to ``0`` under fmt.number().

    Args:
        seconds:  Duration in seconds.  None → null.
        decimals: Fractional digits to keep.  Default 2.
        null:     Placeholder returned for None values.

    Examples:
        hours(77)       → '0.02'
        hours(3600)     → '1.00'
        hours(86_400)   → '24.00'
        hours(None)     → '—'
    """
    if seconds is None:
        return null
    return f"{seconds / 3600:,.{decimals}f}"


def ago(
    delta: Optional[timedelta],
    *,
    null:  str = '—',
) -> str:
    """Format an elapsed timedelta as a human-readable age.

    Rounds to the single largest sensible unit — a monitoring gap reads
    better as "29 hours" than "1 day 5 hours 12 minutes".  Used by the
    status-dashboard stale-data banner ("No status updates in {age}").

    Args:
        delta: Elapsed time as a datetime.timedelta.  None → null.
        null:  Placeholder returned for None values.

    Examples:
        ago(timedelta(seconds=30))   → 'less than a minute'
        ago(timedelta(minutes=18))   → '18 minutes'
        ago(timedelta(minutes=75))   → '75 minutes'
        ago(timedelta(hours=29))     → '29 hours'
        ago(timedelta(days=3))       → '3 days'
        ago(None)                    → '—'
    """
    if delta is None:
        return null
    mins = max(delta.total_seconds(), 0) / 60
    if mins < 1:
        return 'less than a minute'
    if mins < 90:
        n, unit = round(mins), 'minute'
    elif mins < 48 * 60:
        n, unit = round(mins / 60), 'hour'
    else:
        n, unit = round(mins / 1440), 'day'
    return f"{n} {unit}{'s' if n != 1 else ''}"


def factor(
    x:        Optional[Union[int, float]],
    *,
    decimals: int  = 2,
    null:     str  = '—',
) -> str:
    """Format a charging multiplier (a QoS / queue factor) as ``×N.NN``.

    Charging factors are small fractional ratios — economy ``0.7``,
    premium ``1.5``, regular ``1.0`` — so they must NOT go through
    :func:`number`, which rounds to whole numbers and would render
    ``0.7`` as ``1`` (indistinguishable from regular).  The leading
    multiplication sign signals "multiplier" rather than "count".

    Args:
        x:        Multiplier value.  None → null.
        decimals: Fractional digits to keep.  Default 2 (matches the
                  hpc-usage-queries plugin's ``qos_factor`` ``.2f`` spec).
        null:     Placeholder returned for None values.

    Examples:
        factor(0.7)   → '×0.70'
        factor(1.5)   → '×1.50'
        factor(1.0)   → '×1.00'
        factor(0.0)   → '×0.00'
        factor(None)  → '—'
    """
    if x is None:
        return null
    return f"×{x:.{decimals}f}"


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

def register_jinja_filters(target) -> None:
    """Register fmt_* Jinja2 filters on a Flask app **or** a bare Environment.

    Call once inside create_app() after the app object is created — or once
    per standalone ``jinja2.Environment`` (``sam.notify.render``), which is
    why this accepts either. The body only ever touches ``.filters`` and
    ``.globals``, so the Flask coupling was never more than the attribute
    lookup now done on the first line.

    Args:
        target: a Flask app (its ``.jinja_env`` is used) or an Environment.

    Filters registered:
        fmt_number  — {{ value | fmt_number }}
                      {{ value | fmt_number(sig_figs=4) }}
                      {{ value | fmt_number(raw=true) }}
        fmt_pct     — {{ value | fmt_pct }}
                      {{ value | fmt_pct(decimals=2) }}
        fmt_date    — {{ value | fmt_date }}
                      {{ value | fmt_date(fmt='%b %Y') }}
        fmt_size    — {{ value | fmt_size }}
        fmt_hours   — {{ seconds | fmt_hours }}
        fmt_factor  — {{ multiplier | fmt_factor }}   → '×0.70'
        fmt_ago     — {{ timedelta | fmt_ago }}       → '29 hours'
    """
    env = getattr(target, 'jinja_env', target)

    env.filters['fmt_number']   = number
    env.filters['fmt_pct']      = pct
    env.filters['fmt_date']     = date_str
    env.filters['fmt_size']     = size
    env.filters['fmt_hours']    = hours
    env.filters['fmt_factor']   = factor
    env.filters['fmt_ago']      = ago
    env.filters['to_local_dt']  = to_local_dt
    # Resource-type allocation unit label ('hours' / 'TiB' / None). Used on
    # the headline "<n> allocated" figures. Usage:
    #   {{ resource.resource_type | alloc_unit(resource.allocated) }}
    from sam.enums import ResourceTypeName
    env.filters['alloc_unit']   = ResourceTypeName.allocation_unit
    # Global (not a filter) so templates can render "{{ local_tz_label() }}"
    # alongside naive-local timestamps that don't go through to_local_dt.
    env.globals['local_tz_label'] = local_tz_label


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


# ---------------------------------------------------------------------------
# Date axes
# ---------------------------------------------------------------------------
#
# `date_str` deliberately stays ISO — a table column wants `2026-07-26`,
# sortable and unambiguous. A chart axis wants the opposite: the parts that
# repeat across every tick are noise, and the space they occupy is the space
# the plot needs.
#
# Measured before this existed, on the status dashboard's user/project chart:
#
#   6h window   07-26 00  07-26 01  07-26 02  07-26 03   <- date on every tick
#   7d window   2026-07-26  2026-07-27  2026-07-28       <- year AND month
#   1y window   2026-09  2026-11  2027-01  2027-03
#
# ...all rotated 30 degrees, so vertical space was being spent to render
# characters identical across every label.
#
# The rule is one line: **the tick carries what changes, a second line carries
# the context, and the context is drawn only where it changes.** The first tick
# always counts as a change, so an axis is never left without its date.
#
#   6h    14:00   15:00   16:00   17:00        1y    Sep   Nov   Jan   Mar
#         Jul 26                                     2026        2027
#
# Note this is derived from the ACTUAL TICK SPACING, not the data's span. They
# usually agree, but the locator has the last word on where ticks land, and a
# formatter that guessed from the span would mislabel whenever they diverged.

#: ``(max median tick delta in seconds, tick_fn, context_fn)``, coarsening.
#: A `context_fn` of None means the tick is already self-describing.
_DATE_TICK_BANDS = (
    (20 * 3600,        lambda d: f'{d:%H:%M}',      lambda d: f'{d:%b} {d.day}'),
    (20 * 86400,       lambda d: f'{d:%b} {d.day}', lambda d: f'{d:%Y}'),
    (300 * 86400,      lambda d: f'{d:%b}',         lambda d: f'{d:%Y}'),
    (float('inf'),     lambda d: f'{d:%Y}',         None),
)


def _date_band(deltas_seconds):
    """Pick a band from the median gap between ticks."""
    if not deltas_seconds:
        # One tick, or none. Day-grain is the safe middle: it names the month
        # and the day, so a lone tick is still readable.
        return _DATE_TICK_BANDS[1]
    ordered = sorted(deltas_seconds)
    median = ordered[len(ordered) // 2]
    for limit, tick_fn, ctx_fn in _DATE_TICK_BANDS:
        if median < limit:
            return limit, tick_fn, ctx_fn
    return _DATE_TICK_BANDS[-1]


def _label_ticks(dates):
    """``[str]`` for a list of tick datetimes — the shared vocabulary.

    Two-line where context changes, one line elsewhere. Used by both the
    matplotlib formatter and the categorical-axis helper, so a chart plotting
    pre-bucketed period strings reads identically to one plotting datetimes.
    """
    deltas = [(b - a).total_seconds() for a, b in zip(dates, dates[1:])]
    _limit, tick_fn, ctx_fn = _date_band(deltas)

    out, prev_ctx = [], None
    for d in dates:
        tick = tick_fn(d)
        ctx = ctx_fn(d) if ctx_fn else None
        if ctx is not None and ctx != prev_ctx:
            out.append(f'{tick}\n{ctx}')
            prev_ctx = ctx
        else:
            out.append(tick)
    return out


_DATE_FORMATTER_CLS = None


def _date_formatter_cls():
    """Build (once) the ``Formatter`` subclass, keeping matplotlib out of this
    module's import path — `sam.fmt` is imported by every CLI invocation."""
    global _DATE_FORMATTER_CLS
    if _DATE_FORMATTER_CLS is None:
        from matplotlib.dates import num2date
        from matplotlib.ticker import Formatter

        class _SpanDateFormatter(Formatter):
            """Labels a whole tick row at once.

            `format_ticks` rather than `__call__` because the vocabulary is a
            property of the row, not of any one tick: the band comes from the
            spacing between ticks, and "context changed" is only answerable
            with the neighbors in hand.

            This is also what makes it correct where `ConciseDateFormatter` is
            not — that one derives its offset label from the LAST tick, so a
            window showing Jul 26-31 gets labeled `2026-Aug`.
            """

            def format_ticks(self, values):
                dates = [num2date(v).replace(tzinfo=None) for v in values]
                return _label_ticks(dates)

            def __call__(self, x, pos=None):
                # Single-value path (cursor readout, `format_data_short`).
                # No neighbors, so no context to suppress.
                return f'{num2date(x).replace(tzinfo=None):%Y-%m-%d %H:%M}'

        _DATE_FORMATTER_CLS = _SpanDateFormatter
    return _DATE_FORMATTER_CLS


def mpl_date_ticks(max_ticks: int = 12):
    """Return ``(locator, formatter)`` for a matplotlib datetime axis.

    Usage:
        loc, fmtr = fmt.mpl_date_ticks(max_ticks=layout.max_ticks)
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(fmtr)

    Labels come out short and horizontal, so callers should NOT also call
    `fig.autofmt_xdate()` — the rotation it applies exists to fit long labels
    that this removes.

    Args:
        max_ticks: upper bound on tick count. Comes from the chart layout, so
                   a phone gets fewer ticks than a dashboard.
    """
    from matplotlib.dates import AutoDateLocator
    return AutoDateLocator(maxticks=max_ticks), _date_formatter_cls()()


#: Grains a pre-bucketed period label can arrive in, longest first. These are
#: the SQL `strftime`/`to_char` formats the job-history plugin groups by.
_PERIOD_LABEL_FORMATS = ('%Y-%m-%d', '%Y-%m', '%Y')


def parse_period_label(label: str) -> Optional[datetime]:
    """Parse a plugin period label (``2026-07-26`` / ``2026-07`` / ``2026``).

    Returns None for anything else — a week or quarter grain, or a label the
    plugin someday spells differently. Callers fall back to the raw string,
    so an unrecognized grain degrades to today's rendering rather than
    raising inside a chart.
    """
    for f in _PERIOD_LABEL_FORMATS:
        try:
            return datetime.strptime(label, f)
        except (ValueError, TypeError):
            continue
    return None


def compact_date_labels(labels: list) -> list:
    """Apply the date-axis vocabulary to **pre-formatted period strings**.

    Charts plotting a categorical axis of `2026-07-26`-style labels — the
    job-history timeline groups server-side, so its x values are band indices,
    not datetimes — cannot use a matplotlib date formatter. This gives them the
    same labels anyway, so two charts on one tab do not disagree about how a
    date looks.

    Returns *labels* unchanged if any of them fails to parse: a half-converted
    axis is worse than a consistent ISO one.
    """
    parsed = [parse_period_label(l) for l in labels]
    if not parsed or any(p is None for p in parsed):
        return list(labels)
    return _label_ticks(parsed)
