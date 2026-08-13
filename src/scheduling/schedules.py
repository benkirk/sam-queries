"""Occurrence predicates — the whole idea, in one pure module.

A schedule here does **not** answer *"is it due now?"*. It answers *"what is
the most recent scheduled instant at or before `now`?"*. That instant, rendered
as a fixed-width string, is the **occurrence key**: dedup key, lock key, and
the ledger's business identifier all at once.

The distinction is the design. A boolean must be asked at exactly the right
moment, which means either a high-rate poll or a scheduler you trust never to
miss a tick. An occurrence key is a *name for the slot*: the dispatcher asks
"what slot are we in?", tries to claim it, and either wins (runs) or loses
(someone already did). Lateness, duplicate dispatchers and manual re-runs all
become expressible instead of dangerous.

**This module is pure.** stdlib only — no SQLAlchemy, no config, no clock
reads, no I/O. Same input, same output, always.
``tests/unit/test_task_ledger.py`` enforces the import boundary.

See ``docs/plans/implemented/SCHEDULED_TASKS.md`` § 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

#: Every schedule is declared in this zone unless it says otherwise. Human
#: intent is "nightly at 2 a.m." *Mountain*, and it should stay 2 a.m. across
#: DST — which is exactly what a fixed UTC offset would fail to do.
DEFAULT_TZ = 'America/Denver'

#: Fixed width, so lexical order is chronological order. Used as the ledger's
#: `occurrence_key`, which is why it must never gain a variable-length field.
KEY_FORMAT = '%Y%m%dT%H%M%SZ'


def occurrence_key(occurrence: datetime) -> str:
    """Render a naive-UTC occurrence as its ledger key.

    >>> occurrence_key(datetime(2026, 8, 10, 8, 15))
    '20260810T081500Z'
    """
    return occurrence.strftime(KEY_FORMAT)


@runtime_checkable
class Schedule(Protocol):
    """What the runner needs from any schedule."""

    def last_occurrence(self, now_utc: datetime) -> Optional[datetime]:
        """Most recent scheduled instant at or before ``now_utc``.

        Naive UTC, truncated to the second, or ``None`` if there is no such
        occurrence. Pure: no clock reads, no I/O.
        """

    def next_occurrence(self, after_utc: datetime) -> Optional[datetime]:
        """The following instant. **Display only** — see the module note."""

    def describe(self) -> str:
        """Human phrasing, e.g. ``daily at 02:15 America/Denver``."""


# ---------------------------------------------------------------------------
# The local <-> UTC seam, where DST is actually handled.
# ---------------------------------------------------------------------------

def _to_utc_naive(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """Convert a naive *local* wall time to naive UTC, resolving DST.

    Two rules, stated explicitly because leaving them implicit is how you get a
    duplicate nightly run once a year:

    **Ambiguous times** (fall back; 01:00-02:00 happens twice). Resolved with
    ``fold=0`` — the *earlier* UTC instant. During the repeated hour both
    passes map the same wall clock to the same UTC instant, so the second one
    claims an already-succeeded occurrence key and does nothing. The task runs
    once.

    **Nonexistent times** (spring forward; 02:00-03:00 never happens). Shifted
    forward to the first instant that does exist, so ``Daily(2, 15)`` fires
    ~45 minutes late on that one day rather than silently skipping a day.
    ``zoneinfo`` maps a nonexistent local time to a UTC instant that converts
    *back* to a different wall clock; detecting that round-trip mismatch is how
    we know to shift.
    """
    aware = local_naive.replace(tzinfo=tz, fold=0)
    utc = aware.astimezone(timezone.utc).replace(tzinfo=None)

    # Round-trip check: for a nonexistent local time the wall clock we get back
    # is not the one we asked for.
    back = utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
    if back != local_naive:
        # Walk forward minute by minute to the first existing wall time. The
        # gap is an hour at most, so this is bounded and cheap.
        probe = local_naive
        for _ in range(180):
            probe += timedelta(minutes=1)
            aware = probe.replace(tzinfo=tz, fold=0)
            utc = aware.astimezone(timezone.utc).replace(tzinfo=None)
            back = utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
            if back == probe:
                return utc
    return utc


def _to_local_naive(utc_naive: datetime, tz: ZoneInfo) -> datetime:
    """Naive UTC to naive local wall time."""
    return (utc_naive.replace(tzinfo=timezone.utc)
            .astimezone(tz).replace(tzinfo=None))


@dataclass(frozen=True)
class _LocalWallSchedule:
    """Shared machinery for schedules expressed as a local wall clock.

    Subclasses answer one question — :meth:`_candidates_on`, "what wall times
    does this schedule name on this local date?" — and inherit the search,
    the DST handling and the UTC canonicalization.
    """

    #: Keyword-only, and that is load-bearing. Dataclass inheritance places
    #: base fields *before* subclass fields, so a positional `tz` here would
    #: make `Daily(2, 15)` bind tz=2, hour=15 — a schedule that silently runs
    #: at the wrong time and raises nothing.
    tz: str = field(default=DEFAULT_TZ, kw_only=True)

    # How many local days back the search may walk before giving up. A month
    # plus slack, so MonthlyDay always resolves.
    _MAX_LOOKBACK_DAYS = 40

    @property
    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def _candidates_on(self, local_date) -> list[tuple[int, int]]:
        """``(hour, minute)`` pairs this schedule names on ``local_date``."""
        raise NotImplementedError

    def last_occurrence(self, now_utc: datetime) -> Optional[datetime]:
        zone = self._zone
        now_utc = now_utc.replace(microsecond=0)
        local_now = _to_local_naive(now_utc, zone)

        # Walk back day by day in *local* terms and take the newest candidate
        # whose UTC instant is <= now. Days are searched rather than computed
        # because a candidate's UTC instant is not monotonic in wall time
        # across a DST boundary.
        for back in range(self._MAX_LOOKBACK_DAYS + 1):
            day = (local_now - timedelta(days=back)).date()
            best = None
            for hour, minute in self._candidates_on(day):
                local = datetime(day.year, day.month, day.day, hour, minute)
                utc = _to_utc_naive(local, zone)
                if utc <= now_utc and (best is None or utc > best):
                    best = utc
            if best is not None:
                return best
        return None

    def next_occurrence(self, after_utc: datetime) -> Optional[datetime]:
        """**Display only.** Nothing in the control flow may call this.

        A scheduler that reasons forward must be right about when it wakes up,
        and this one is deliberately not: it wakes hourly and asks what slot it
        is in. This exists to render a "next due" column.
        """
        zone = self._zone
        after_utc = after_utc.replace(microsecond=0)
        local_after = _to_local_naive(after_utc, zone)

        for fwd in range(self._MAX_LOOKBACK_DAYS + 1):
            day = (local_after + timedelta(days=fwd)).date()
            best = None
            for hour, minute in self._candidates_on(day):
                local = datetime(day.year, day.month, day.day, hour, minute)
                utc = _to_utc_naive(local, zone)
                if utc > after_utc and (best is None or utc < best):
                    best = utc
            if best is not None:
                return best
        return None


@dataclass(frozen=True)
class Hourly:
    """Every hour at ``:minute``, on the **UTC** clock.

    Alone among these, `Hourly` takes no ``tz`` — and does not merely ignore
    one, it does not accept one, so nobody can pass a zone and believe it
    means something. Two reasons:

    * every zone SAM cares about is offset from UTC by a whole number of
      hours, so ":15 local" and ":15 UTC" name the same instants anyway; and
    * computing in UTC means a DST transition can neither duplicate nor drop
      an hourly slot. A local-wall hourly schedule loses one slot each fall
      (the repeated hour folds onto one instant) and risks merging one each
      spring. Twenty-four clean slots a day is worth more than a nominal
      symmetry with `Daily`.
    """

    minute: int = 0

    def __post_init__(self):
        if not 0 <= self.minute <= 59:
            raise ValueError(f'minute must be 0..59, got {self.minute}')

    def last_occurrence(self, now_utc: datetime) -> Optional[datetime]:
        now_utc = now_utc.replace(microsecond=0)
        candidate = now_utc.replace(minute=self.minute, second=0)
        if candidate > now_utc:
            candidate -= timedelta(hours=1)
        return candidate

    def next_occurrence(self, after_utc: datetime) -> Optional[datetime]:
        after_utc = after_utc.replace(microsecond=0)
        candidate = after_utc.replace(minute=self.minute, second=0)
        while candidate <= after_utc:
            candidate += timedelta(hours=1)
        return candidate

    def describe(self) -> str:
        return f'hourly at :{self.minute:02d} UTC'


@dataclass(frozen=True)
class Daily(_LocalWallSchedule):
    """Every day at ``hour:minute`` in :attr:`tz`."""

    hour: int = 0
    minute: int = 0

    def __post_init__(self):
        if not 0 <= self.hour <= 23:
            raise ValueError(f'hour must be 0..23, got {self.hour}')
        if not 0 <= self.minute <= 59:
            raise ValueError(f'minute must be 0..59, got {self.minute}')

    def _candidates_on(self, local_date) -> list[tuple[int, int]]:
        return [(self.hour, self.minute)]

    def describe(self) -> str:
        return f'daily at {self.hour:02d}:{self.minute:02d} {self.tz}'


@dataclass(frozen=True)
class Weekly(_LocalWallSchedule):
    """Every ``weekday`` at ``hour:minute``. ``weekday`` is 0=Mon .. 6=Sun."""

    weekday: int = 0
    hour: int = 0
    minute: int = 0

    def __post_init__(self):
        if not 0 <= self.weekday <= 6:
            raise ValueError(f'weekday must be 0..6 (Mon..Sun), got {self.weekday}')
        if not 0 <= self.hour <= 23:
            raise ValueError(f'hour must be 0..23, got {self.hour}')
        if not 0 <= self.minute <= 59:
            raise ValueError(f'minute must be 0..59, got {self.minute}')

    def _candidates_on(self, local_date) -> list[tuple[int, int]]:
        if local_date.weekday() != self.weekday:
            return []
        return [(self.hour, self.minute)]

    def describe(self) -> str:
        names = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                 'Saturday', 'Sunday')
        return (f'weekly on {names[self.weekday]} at '
                f'{self.hour:02d}:{self.minute:02d} {self.tz}')


@dataclass(frozen=True)
class MonthlyDay(_LocalWallSchedule):
    """Day-of-month at ``hour:minute``.

    ``day`` may be **negative to count from the end**: ``-1`` is the last day
    of the month, ``-2`` the second to last.

    A positive ``day`` of 29, 30 or 31 in a shorter month **clamps to the last
    day** rather than skipping. That is a documented choice, not an accident:
    "run on the 31st" almost always means "run at the end of the month", and a
    schedule that silently skips February is a worse surprise than one that
    fires a day or three early.
    """

    day: int = 1
    hour: int = 0
    minute: int = 0

    def __post_init__(self):
        if self.day == 0 or not -28 <= self.day <= 31:
            raise ValueError(
                f'day must be 1..31 or -1..-28 (from the end), got {self.day}')
        if not 0 <= self.hour <= 23:
            raise ValueError(f'hour must be 0..23, got {self.hour}')
        if not 0 <= self.minute <= 59:
            raise ValueError(f'minute must be 0..59, got {self.minute}')

    @staticmethod
    def _days_in_month(year: int, month: int) -> int:
        if month == 12:
            nxt = datetime(year + 1, 1, 1)
        else:
            nxt = datetime(year, month + 1, 1)
        return (nxt - timedelta(days=1)).day

    def _target_day(self, year: int, month: int) -> int:
        span = self._days_in_month(year, month)
        if self.day > 0:
            return min(self.day, span)          # clamp; see the docstring
        return span + 1 + self.day              # -1 -> last day

    def _candidates_on(self, local_date) -> list[tuple[int, int]]:
        if local_date.day != self._target_day(local_date.year, local_date.month):
            return []
        return [(self.hour, self.minute)]

    def describe(self) -> str:
        if self.day < 0:
            which = {-1: 'last day'}.get(self.day, f'{-self.day} days from the end')
        else:
            which = f'day {self.day}'
        return (f'monthly on {which} at '
                f'{self.hour:02d}:{self.minute:02d} {self.tz}')


@dataclass(frozen=True)
class CronExpr:
    """A raw 5-field cron expression: ``minute hour dom month dow``.

    Supports ``*``, ``a,b``, ``a-b``, and ``*/n`` (also ``a-b/n``) in each
    field. Day-of-month and day-of-week are OR'd when both are restricted,
    matching Vixie cron.

    Implemented as a **bounded backward scan**: from ``now`` truncated to the
    minute, step back a minute at a time and return the first match, raising
    after :attr:`horizon`. At most 2,880 integer comparisons for the default
    48-hour bound, and it buys the escape hatch with **no new runtime
    dependency**. The cost is an honest restriction — *a CronExpr must fire at
    least once every `horizon`* — and anything rarer should use a named
    predicate, which is better documentation anyway.
    """

    expr: str = '0 * * * *'
    horizon: timedelta = timedelta(hours=48)
    tz: str = DEFAULT_TZ

    def __post_init__(self):
        # Parse eagerly so a bad expression fails at import/registration time,
        # not at 02:15 in a pod.
        object.__setattr__(self, '_fields', _parse_cron(self.expr))

    @property
    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def _matches(self, local: datetime) -> bool:
        minutes, hours, doms, months, dows = self._fields   # type: ignore[attr-defined]
        if local.minute not in minutes or local.hour not in hours:
            return False
        if local.month not in months:
            return False
        # cron's day fields: if both are restricted, either may match.
        dom_restricted = len(doms) != 31
        dow_restricted = len(dows) != 7
        dom_ok = local.day in doms
        # cron uses 0=Sunday; datetime uses 0=Monday.
        dow_ok = ((local.weekday() + 1) % 7) in dows
        if dom_restricted and dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def last_occurrence(self, now_utc: datetime) -> Optional[datetime]:
        zone = self._zone
        probe = now_utc.replace(second=0, microsecond=0)
        steps = int(self.horizon.total_seconds() // 60)
        for _ in range(steps + 1):
            if self._matches(_to_local_naive(probe, zone)):
                return probe
            probe -= timedelta(minutes=1)
        raise ValueError(
            f'CronExpr({self.expr!r}) found no occurrence within {self.horizon} '
            f'of {now_utc.isoformat()}. This class is a bounded backward scan; '
            f'a schedule rarer than its horizon needs a named predicate '
            f'(Daily/Weekly/MonthlyDay) or a larger horizon=.')

    def next_occurrence(self, after_utc: datetime) -> Optional[datetime]:
        zone = self._zone
        probe = after_utc.replace(second=0, microsecond=0) + timedelta(minutes=1)
        steps = int(self.horizon.total_seconds() // 60)
        for _ in range(steps + 1):
            if self._matches(_to_local_naive(probe, zone)):
                return probe
            probe += timedelta(minutes=1)
        return None

    def describe(self) -> str:
        return f'cron {self.expr!r} ({self.tz})'


_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _parse_field(spec: str, low: int, high: int) -> frozenset[int]:
    """One cron field to the set of values it matches."""
    values: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            raise ValueError(f'empty cron field element in {spec!r}')

        step = 1
        if '/' in part:
            part, _, step_s = part.partition('/')
            try:
                step = int(step_s)
            except ValueError:
                raise ValueError(f'bad cron step {step_s!r} in {spec!r}') from None
            if step < 1:
                raise ValueError(f'cron step must be >= 1, got {step}')

        if part == '*':
            start, end = low, high
        elif '-' in part.lstrip('-'):
            start_s, _, end_s = part.partition('-')
            try:
                start, end = int(start_s), int(end_s)
            except ValueError:
                raise ValueError(f'bad cron range {part!r} in {spec!r}') from None
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise ValueError(f'bad cron value {part!r} in {spec!r}') from None

        if start < low or end > high or start > end:
            raise ValueError(
                f'cron value {part!r} out of range {low}..{high} in {spec!r}')
        values.update(range(start, end + 1, step))

    if not values:
        raise ValueError(f'cron field {spec!r} matches nothing')
    return frozenset(values)


def _parse_cron(expr: str) -> tuple[frozenset[int], ...]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f'cron expression must have 5 fields '
            f'(minute hour dom month dow), got {len(parts)}: {expr!r}')
    return tuple(_parse_field(p, low, high)
                 for p, (low, high) in zip(parts, _CRON_BOUNDS))
