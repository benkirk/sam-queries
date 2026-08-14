"""The occurrence predicates — pure functions, no fixtures, no database.

The DST cases are the reason this file is long. `Daily(2, 15)` in
`America/Denver` is deliberately scheduled *inside* the window where both
transitions bite, because the alternative — moving the nightly prune to dodge
a tested code path — is superstition. So the path has to actually be tested.

Transition dates used throughout (America/Denver):
  * spring forward  2027-03-14, 02:00 MST -> 03:00 MDT  (02:00-02:59 does not exist)
  * fall back       2026-11-01, 02:00 MDT -> 01:00 MST  (01:00-01:59 happens twice)
"""

from datetime import datetime, timedelta

import pytest

from scheduling.schedules import (
    KEY_FORMAT,
    BusinessHourly,
    CronExpr,
    Daily,
    Hourly,
    MonthlyDay,
    Schedule,
    Weekly,
    occurrence_key,
)

pytestmark = pytest.mark.unit


def sweep(sched, start, *, hours, step_minutes=60):
    """Every distinct slot an hourly dispatcher would see over a window.

    Returns them sorted. Note a window of exactly N days sees N+1 daily slots
    when it starts and ends after that day's slot — which is why these tests
    assert *spacing*, not a count.
    """
    seen = set()
    for m in range(0, hours * 60 + 1, step_minutes):
        occ = sched.last_occurrence(start + timedelta(minutes=m))
        if occ is not None:
            seen.add(occ)
    return sorted(seen)


def assert_evenly_spaced(slots, *, expected: timedelta, label: str):
    """No period skipped and none doubled.

    This is the property that actually matters, and the one DST threatens:
    a gap of 2x means a day was missed, a gap of 0 means it fired twice.
    """
    assert len(slots) >= 2, f'{label}: too few slots to check spacing'
    gaps = [b - a for a, b in zip(slots, slots[1:])]
    bad = [(a, b, b - a) for a, b in zip(slots, slots[1:]) if b - a != expected]
    assert not bad, (
        f'{label}: expected every gap to be {expected}, but found {bad}. '
        f'A larger gap means a period was skipped; a smaller one means it '
        f'fired twice.\nslots={slots}\ngaps={gaps}')


def assert_daily_ish(slots, *, label: str):
    """One slot per calendar day, every gap within an hour of 24h.

    The DST-tolerant form of :func:`assert_evenly_spaced`: a transition
    legitimately stretches or shrinks one interval by an hour, but a *skipped*
    day shows up as ~48h and a *double fire* as ~0h, and both are bugs.
    """
    assert len(slots) >= 2, f'{label}: too few slots to check'
    dates = [s.date() for s in slots]
    assert len(dates) == len(set(dates)), f'{label}: a day fired twice: {dates}'
    for a, b in zip(slots, slots[1:]):
        gap = b - a
        assert timedelta(hours=22) <= gap <= timedelta(hours=26), (
            f'{label}: gap of {gap} between {a} and {b} — a day was skipped '
            f'or doubled.\nslots={slots}')


ALL_SCHEDULES = [
    Hourly(0),
    Hourly(7),
    BusinessHourly(),
    BusinessHourly(30, 9, 16),
    Daily(2, 15),
    Daily(23, 59),
    Weekly(0, 3, 0),
    Weekly(6, 12, 30),
    MonthlyDay(1, 4, 0),
    MonthlyDay(-1, 4, 0),
    MonthlyDay(31, 4, 0),
    CronExpr('7 * * * *'),
]


# ------------------------------------------------------------ the contract

class TestProtocol:

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_satisfies_the_schedule_protocol(self, sched):
        assert isinstance(sched, Schedule)

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_last_occurrence_is_at_or_before_now(self, sched):
        now = datetime(2026, 8, 12, 9, 7, 3)
        occ = sched.last_occurrence(now)
        assert occ is not None
        assert occ <= now

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_last_occurrence_is_idempotent(self, sched):
        """Asking again *at the occurrence itself* returns the same instant.

        This is what makes the occurrence key stable: a dispatcher that runs
        late and one that runs on time must name the same slot.
        """
        now = datetime(2026, 8, 12, 9, 7, 3)
        first = sched.last_occurrence(now)
        assert sched.last_occurrence(first) == first

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_is_pure(self, sched):
        """Same input, same output — no clock reads inside."""
        now = datetime(2026, 5, 4, 17, 42, 19)
        assert sched.last_occurrence(now) == sched.last_occurrence(now)

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_seconds_are_truncated(self, sched):
        occ = sched.last_occurrence(datetime(2026, 8, 12, 9, 7, 3, 500000))
        assert occ.second == 0 and occ.microsecond == 0

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_next_occurrence_is_strictly_after(self, sched):
        now = datetime(2026, 8, 12, 9, 7, 3)
        assert sched.next_occurrence(now) > now

    @pytest.mark.parametrize('sched', ALL_SCHEDULES, ids=repr)
    def test_describe_is_a_nonempty_string(self, sched):
        assert isinstance(sched.describe(), str) and sched.describe()


class TestOccurrenceKey:

    def test_is_fixed_width_and_sorts_chronologically(self):
        keys = [occurrence_key(datetime(2026, 1, 2, 3, 4, 5)),
                occurrence_key(datetime(2026, 11, 30, 23, 59, 0)),
                occurrence_key(datetime(2027, 1, 1, 0, 0, 0))]
        assert len(set(len(k) for k in keys)) == 1, 'fixed width'
        assert keys == sorted(keys), 'lexical order == chronological order'

    def test_fits_the_ledger_column(self):
        """`task_run.occurrence_key` is String(24); a manual key adds one char."""
        assert len(occurrence_key(datetime(2026, 8, 10, 8, 15))) + 1 <= 24

    def test_format_round_trips(self):
        occ = datetime(2026, 8, 10, 8, 15)
        assert datetime.strptime(occurrence_key(occ), KEY_FORMAT) == occ


# --------------------------------------------------------- positional args

class TestConstructorBinding:
    """Dataclass inheritance puts base fields first, so an inherited
    positional `tz` would make `Daily(2, 15)` mean `tz=2, hour=15`."""

    def test_daily_positional_args_are_hour_minute(self):
        d = Daily(2, 15)
        assert (d.hour, d.minute, d.tz) == (2, 15, 'America/Denver')

    def test_weekly_positional_args(self):
        w = Weekly(3, 9, 30)
        assert (w.weekday, w.hour, w.minute) == (3, 9, 30)

    def test_monthly_positional_args(self):
        m = MonthlyDay(-1, 4, 5)
        assert (m.day, m.hour, m.minute) == (-1, 4, 5)

    def test_tz_is_keyword_only(self):
        assert Daily(2, 15, tz='UTC').tz == 'UTC'


class TestValidation:

    @pytest.mark.parametrize('kwargs', [
        {'hour': 24, 'minute': 0}, {'hour': -1, 'minute': 0},
        {'hour': 0, 'minute': 60}, {'hour': 0, 'minute': -1},
    ])
    def test_daily_rejects_out_of_range(self, kwargs):
        with pytest.raises(ValueError):
            Daily(**kwargs)

    @pytest.mark.parametrize('day', [0, 32, -29])
    def test_monthly_rejects_impossible_day(self, day):
        with pytest.raises(ValueError):
            MonthlyDay(day)

    @pytest.mark.parametrize('weekday', [-1, 7])
    def test_weekly_rejects_bad_weekday(self, weekday):
        with pytest.raises(ValueError):
            Weekly(weekday)

    @pytest.mark.parametrize('minute', [-1, 60])
    def test_hourly_rejects_bad_minute(self, minute):
        with pytest.raises(ValueError):
            Hourly(minute)

    @pytest.mark.parametrize('kwargs', [
        {'minute': 60}, {'minute': -1},
        {'start_hour': 24}, {'end_hour': -1},
        # end_hour is INCLUSIVE, so start > end is the empty window.
        {'start_hour': 17, 'end_hour': 8},
        {'weekdays': ()},
        {'weekdays': (0, 7)},
    ])
    def test_business_hourly_rejects_a_bad_window(self, kwargs):
        with pytest.raises(ValueError):
            BusinessHourly(**kwargs)

    def test_business_hourly_allows_a_single_hour_window(self):
        """start == end is one slot a day, not an error — the boundary the
        `start_hour > end_hour` check has to get right."""
        assert BusinessHourly(0, 9, 9)._candidates_on(
            datetime(2026, 8, 12).date()) == [(9, 0)]


# --------------------------------------------------------------- semantics

class TestDaily:

    def test_mdt_offset(self):
        """02:15 MDT (UTC-6) is 08:15 UTC."""
        occ = Daily(2, 15).last_occurrence(datetime(2026, 8, 12, 9, 7, 3))
        assert occ == datetime(2026, 8, 12, 8, 15)

    def test_mst_offset(self):
        """02:15 MST (UTC-7) is 09:15 UTC — the same wall clock, in winter."""
        occ = Daily(2, 15).last_occurrence(datetime(2026, 12, 12, 15, 0, 0))
        assert occ == datetime(2026, 12, 12, 9, 15)

    def test_before_todays_slot_returns_yesterdays(self):
        occ = Daily(2, 15).last_occurrence(datetime(2026, 8, 12, 7, 0, 0))
        assert occ == datetime(2026, 8, 11, 8, 15)

    def test_exactly_at_the_slot_returns_it(self):
        occ = Daily(2, 15).last_occurrence(datetime(2026, 8, 12, 8, 15, 0))
        assert occ == datetime(2026, 8, 12, 8, 15)

    def test_a_simulated_week_fires_once_a_day(self):
        slots = sweep(Daily(2, 15), datetime(2026, 8, 5, 12, 0), hours=7 * 24)
        assert_evenly_spaced(slots, expected=timedelta(days=1),
                             label='ordinary week')


class TestHourly:

    def test_takes_no_tz(self):
        """Deliberate — see the class docstring."""
        with pytest.raises(TypeError):
            Hourly(0, tz='UTC')

    def test_rolls_back_when_the_slot_is_still_ahead(self):
        occ = Hourly(30).last_occurrence(datetime(2026, 8, 12, 9, 7))
        assert occ == datetime(2026, 8, 12, 8, 30)

    def test_returns_this_hour_once_past_the_slot(self):
        occ = Hourly(30).last_occurrence(datetime(2026, 8, 12, 9, 45))
        assert occ == datetime(2026, 8, 12, 9, 30)

    def test_slots_stay_one_hour_apart_across_a_dst_transition(self):
        """The reason Hourly is UTC-based rather than local-wall.

        A local-wall hourly schedule folds two slots onto one instant each
        fall — this sweep spans 2026-11-01 and would catch that as a 2-hour
        gap.
        """
        slots = sweep(Hourly(7), datetime(2026, 10, 31, 0, 0),
                      hours=3 * 24, step_minutes=10)
        assert_evenly_spaced(slots, expected=timedelta(hours=1),
                             label='hourly across fall-back')


class TestBusinessHourly:
    """2026-08-12 is a Wednesday; 08:00-17:00 MDT is 14:00-23:00 UTC."""

    def test_ten_slots_on_a_weekday(self):
        slots = sweep(BusinessHourly(), datetime(2026, 8, 12, 14, 0),
                      hours=9, step_minutes=30)
        assert len(slots) == 10, f'expected 08:00..17:00 inclusive, got {slots}'
        assert_evenly_spaced(slots, expected=timedelta(hours=1),
                             label='one business day')

    def test_the_end_hour_is_inclusive(self):
        """17:00 fires. An exclusive `range` here would silently drop it, and
        nothing else in the suite would notice."""
        occ = BusinessHourly().last_occurrence(datetime(2026, 8, 12, 23, 30))
        assert occ == datetime(2026, 8, 12, 23, 0)      # 17:00 MDT

    def test_before_the_first_slot_returns_yesterdays_last(self):
        # 13:00 UTC is 07:00 MDT Wednesday — before 08:00, so Tuesday 17:00.
        occ = BusinessHourly().last_occurrence(datetime(2026, 8, 12, 13, 0))
        assert occ == datetime(2026, 8, 11, 23, 0)

    def test_after_the_last_slot_returns_todays_last(self):
        # 06:00 UTC Thursday is 00:00 MDT — still Wednesday's 17:00 locally.
        occ = BusinessHourly().last_occurrence(datetime(2026, 8, 13, 6, 0))
        assert occ == datetime(2026, 8, 12, 23, 0)

    @pytest.mark.parametrize('when', [
        datetime(2026, 8, 15, 20, 0),       # Saturday 14:00 MDT
        datetime(2026, 8, 16, 20, 0),       # Sunday 14:00 MDT
        datetime(2026, 8, 17, 13, 0),       # Monday 07:00 MDT, before 08:00
    ])
    def test_the_weekend_holds_fridays_last_slot(self, when):
        """The whole weekend answers "Friday 17:00", which is what makes a
        Friday-afternoon arrival send on Monday morning rather than Saturday."""
        occ = BusinessHourly().last_occurrence(when)
        assert occ == datetime(2026, 8, 14, 23, 0)      # Fri 17:00 MDT

    def test_monday_opens_at_the_first_slot(self):
        occ = BusinessHourly().last_occurrence(datetime(2026, 8, 17, 14, 0))
        assert occ == datetime(2026, 8, 17, 14, 0)      # Mon 08:00 MDT

    def test_next_occurrence_from_friday_evening_is_monday_morning(self):
        nxt = BusinessHourly().next_occurrence(datetime(2026, 8, 14, 23, 30))
        assert nxt == datetime(2026, 8, 17, 14, 0)

    def test_describe(self):
        assert BusinessHourly().describe() == (
            'hourly 08:00-17:00 Mon-Fri America/Denver')

    def test_describe_lists_a_non_contiguous_window(self):
        assert BusinessHourly(30, 9, 10, weekdays=(0, 2, 4)).describe() == (
            'hourly 09:30-10:30 Mon,Wed,Fri America/Denver')


class TestBusinessHourlyAndDst:
    """The immunity is a property of the WINDOW, not of the class.

    Both halves are asserted on the *same* transition dates, with the *same*
    class, differing only in `start_hour`/`end_hour`. US transitions are always
    on a Sunday, so these use ``weekdays=(6,)`` to put the window on the
    transition day at all — with the default Mon-Fri the question cannot even
    be posed, which is itself worth knowing.
    """

    #: 2026-11-01 (fall back) and 2027-03-14 (spring forward) are both Sundays.
    FALL_BACK = datetime(2026, 11, 1)
    SPRING_FORWARD = datetime(2027, 3, 14)

    def _slots_on(self, sched, day, *, from_utc, hours):
        return sweep(sched, from_utc, hours=hours, step_minutes=15)

    def test_the_business_window_is_untouched_by_the_fall_back(self):
        """08:00-17:00 MST on 2026-11-01 is 15:00-00:00 UTC: ten clean slots."""
        slots = self._slots_on(BusinessHourly(weekdays=(6,)), self.FALL_BACK,
                               from_utc=datetime(2026, 11, 1, 15, 0), hours=9)
        assert len(slots) == 10
        assert_evenly_spaced(slots, expected=timedelta(hours=1),
                             label='fall-back day, business window')

    def test_the_business_window_is_untouched_by_the_spring_forward(self):
        """08:00-17:00 MDT on 2027-03-14 is 14:00-23:00 UTC."""
        slots = self._slots_on(BusinessHourly(weekdays=(6,)),
                               self.SPRING_FORWARD,
                               from_utc=datetime(2027, 3, 14, 14, 0), hours=9)
        assert len(slots) == 10
        assert_evenly_spaced(slots, expected=timedelta(hours=1),
                             label='spring-forward day, business window')

    def test_an_overnight_window_LOSES_a_slot_at_the_fall_back(self):
        """The inverse case, and the reason the docstring warns about it.

        01:00-05:00 names five candidates. Local 01:00 happens twice and
        `fold=0` resolves both to the earlier instant, so the repeated hour
        yields ONE slot and the 01:00 -> 02:00 step is two hours of UTC.
        """
        slots = self._slots_on(BusinessHourly(0, 1, 5, weekdays=(6,)),
                               self.FALL_BACK,
                               from_utc=datetime(2026, 11, 1, 7, 0), hours=5)
        assert len(slots) == 5
        gaps = [b - a for a, b in zip(slots, slots[1:])]
        assert gaps.count(timedelta(hours=2)) == 1, (
            f'expected exactly one folded slot on the fall-back day, got '
            f'{gaps} from {slots}')

    def test_an_overnight_window_MERGES_two_slots_at_the_spring_forward(self):
        """The other half of `Hourly`'s warning.

        Local 02:00 does not exist and shifts forward to 03:00, which is
        already a candidate — so five candidates name four distinct instants.
        """
        sched = BusinessHourly(0, 1, 5, weekdays=(6,))
        slots = self._slots_on(sched, self.SPRING_FORWARD,
                               from_utc=datetime(2027, 3, 14, 8, 0), hours=5)
        assert len(slots) == 4, (
            f'expected the 02:00 candidate to merge onto 03:00, got {slots}')


class TestWeekly:

    def test_picks_the_named_weekday(self):
        # 2026-08-12 is a Wednesday; the previous Monday is 2026-08-10.
        occ = Weekly(0, 3, 0).last_occurrence(datetime(2026, 8, 12, 9, 0))
        assert occ.date() == datetime(2026, 8, 10).date()

    def test_sunday_is_six(self):
        occ = Weekly(6, 12, 0).last_occurrence(datetime(2026, 8, 12, 9, 0))
        # 2026-08-09 is the preceding Sunday.
        assert occ.date() == datetime(2026, 8, 9).date()


class TestMonthlyDay:

    def test_last_day_of_a_31_day_month(self):
        occ = MonthlyDay(-1, 4, 0).last_occurrence(datetime(2026, 8, 15, 12, 0))
        assert occ.date() == datetime(2026, 7, 31).date()

    def test_last_day_of_february_non_leap(self):
        occ = MonthlyDay(-1, 4, 0).last_occurrence(datetime(2026, 3, 15, 12, 0))
        assert occ.date() == datetime(2026, 2, 28).date()

    def test_last_day_of_february_leap(self):
        occ = MonthlyDay(-1, 4, 0).last_occurrence(datetime(2028, 3, 15, 12, 0))
        assert occ.date() == datetime(2028, 2, 29).date()

    def test_day_31_clamps_in_a_30_day_month(self):
        """Documented behaviour: clamp, never skip."""
        occ = MonthlyDay(31, 4, 0).last_occurrence(datetime(2026, 7, 5, 12, 0))
        assert occ.date() == datetime(2026, 6, 30).date()

    def test_day_31_clamps_in_february(self):
        occ = MonthlyDay(31, 4, 0).last_occurrence(datetime(2026, 3, 5, 12, 0))
        assert occ.date() == datetime(2026, 2, 28).date()

    def test_day_29_clamps_in_february_non_leap(self):
        occ = MonthlyDay(29, 4, 0).last_occurrence(datetime(2026, 3, 5, 12, 0))
        assert occ.date() == datetime(2026, 2, 28).date()

    def test_day_29_is_exact_in_a_leap_february(self):
        occ = MonthlyDay(29, 4, 0).last_occurrence(datetime(2028, 3, 5, 12, 0))
        assert occ.date() == datetime(2028, 2, 29).date()

    def test_exactly_one_slot_per_calendar_month(self):
        sched = MonthlyDay(-1, 4, 0)
        slots = sweep(sched, datetime(2026, 1, 15, 12, 0),
                      hours=365 * 24, step_minutes=24 * 60)
        months = [(s.year, s.month) for s in slots]
        assert len(months) == len(set(months)), (
            f'a month fired twice: {months}')
        # Every slot is genuinely the last day of its month.
        for s in slots:
            assert (s + timedelta(days=1)).month != s.month, s
        # And no month in the swept range is missing.
        assert months == sorted(months)
        for (y1, m1), (y2, m2) in zip(months, months[1:]):
            assert (y2 * 12 + m2) - (y1 * 12 + m1) == 1, (
                f'gap between {y1}-{m1} and {y2}-{m2}')


# --------------------------------------------------------------------- DST

class TestSpringForward:
    """2027-03-14: 02:00 MST jumps to 03:00 MDT. 02:15 local does not exist."""

    def test_nonexistent_slot_shifts_forward_and_does_not_skip_the_day(self):
        sched = Daily(2, 15)
        # Late on the transition day, UTC. 03:00 MDT == 09:00 UTC.
        occ = sched.last_occurrence(datetime(2027, 3, 14, 20, 0))
        assert occ is not None
        assert occ.date() == datetime(2027, 3, 14).date(), 'the day was skipped'
        assert occ == datetime(2027, 3, 14, 9, 0), (
            'a nonexistent 02:15 must shift forward to the first instant that '
            'exists, i.e. 03:00 MDT = 09:00 UTC')

    def test_the_week_around_the_gap_skips_no_day(self):
        """One slot per calendar day, with every gap still close to 24h.

        Two gaps are irregular here, not one: the shifted slot lands at 09:00
        UTC between a 09:15 (MST) and an 08:15 (MDT), giving 23h45m then
        23h15m. The invariant worth pinning is not the exact figure but that
        nothing approaches 48h (a skipped day) or 0h (a double fire). The
        exact shifted instant is pinned by
        `test_nonexistent_slot_shifts_forward_and_does_not_skip_the_day`.
        """
        assert_daily_ish(sweep(Daily(2, 15), datetime(2027, 3, 11, 12, 0),
                               hours=7 * 24), label='spring-forward week')


class TestFallBack:
    """2026-11-01: 02:00 MDT falls back to 01:00 MST. 01:00-01:59 twice."""

    def test_ambiguous_slot_resolves_to_the_earlier_instant(self):
        sched = Daily(1, 30)
        occ = sched.last_occurrence(datetime(2026, 11, 1, 20, 0))
        # fold=0 -> MDT (UTC-6) -> 07:30 UTC, not 08:30.
        assert occ == datetime(2026, 11, 1, 7, 30)

    def test_the_repeated_hour_yields_one_occurrence_not_two(self):
        """The duplicate-nightly-run bug this rule exists to prevent."""
        sched = Daily(1, 30)
        seen = set()
        # Sweep the whole repeated hour in UTC: 07:00 -> 09:00 UTC covers both
        # the MDT and MST readings of 01:30 local.
        probe = datetime(2026, 11, 1, 7, 0)
        while probe <= datetime(2026, 11, 1, 9, 0):
            occ = sched.last_occurrence(probe)
            if occ is not None and occ.date() == datetime(2026, 11, 1).date():
                seen.add(occ)
            probe += timedelta(minutes=5)
        assert len(seen) == 1, f'fired {len(seen)} times on the fall-back day: {seen}'

    def test_the_week_around_the_fold_doubles_no_day(self):
        """One 25-hour gap (the clock gained an hour), never a 0-hour one."""
        slots = sweep(Daily(2, 15), datetime(2026, 10, 29, 12, 0), hours=7 * 24)
        assert_daily_ish(slots, label='fall-back week')
        gaps = [b - a for a, b in zip(slots, slots[1:])]
        assert gaps.count(timedelta(hours=25)) == 1, (
            f'expected exactly one 25h interval across the fold, got {gaps}')


# ---------------------------------------------------------------- CronExpr

class TestCronExpr:

    def test_agrees_with_hourly_over_many_instants(self):
        """`7 * * * *` and `Hourly(7)` must name identical slots."""
        cron, hourly = CronExpr('7 * * * *'), Hourly(7)
        probe = datetime(2026, 6, 1, 0, 0)
        for i in range(1000):
            t = probe + timedelta(minutes=i * 7)
            assert cron.last_occurrence(t) == hourly.last_occurrence(t), t

    def test_agrees_with_daily_in_utc(self):
        cron = CronExpr('15 2 * * *', tz='UTC')
        daily = Daily(2, 15, tz='UTC')
        probe = datetime(2026, 6, 1, 0, 0)
        for i in range(200):
            t = probe + timedelta(hours=i * 3)
            assert cron.last_occurrence(t) == daily.last_occurrence(t), t

    def test_step_syntax(self):
        cron = CronExpr('*/15 * * * *')
        occ = cron.last_occurrence(datetime(2026, 8, 12, 9, 37))
        assert occ == datetime(2026, 8, 12, 9, 30)

    def test_list_and_range_syntax(self):
        cron = CronExpr('0,30 9-17 * * *', tz='UTC')
        assert cron.last_occurrence(datetime(2026, 8, 12, 14, 45)) == \
            datetime(2026, 8, 12, 14, 30)

    def test_raises_past_its_horizon(self):
        """A schedule rarer than the bound must fail loudly, not silently."""
        # 03:00 on 1 January only, with a 48h horizon, asked in August.
        cron = CronExpr('0 3 1 1 *', horizon=timedelta(hours=48), tz='UTC')
        with pytest.raises(ValueError, match='no occurrence within'):
            cron.last_occurrence(datetime(2026, 8, 12, 9, 0))

    def test_the_error_names_the_remedy(self):
        cron = CronExpr('0 3 1 1 *', horizon=timedelta(hours=48), tz='UTC')
        with pytest.raises(ValueError) as exc:
            cron.last_occurrence(datetime(2026, 8, 12, 9, 0))
        assert 'named predicate' in str(exc.value)

    @pytest.mark.parametrize('expr', [
        '* * * *',            # 4 fields
        '* * * * * *',        # 6 fields
        '60 * * * *',         # minute out of range
        '* 24 * * *',         # hour out of range
        '* * 0 * *',          # dom out of range
        '* * * 13 *',         # month out of range
        '* * * * 7',          # dow out of range
        'x * * * *',          # not a number
        '*/0 * * * *',        # zero step
    ])
    def test_rejects_bad_expressions_at_construction(self, expr):
        with pytest.raises(ValueError):
            CronExpr(expr)

    def test_dom_and_dow_are_ored_when_both_restricted(self):
        """Vixie cron semantics: `1 * 13 * 5` matches the 13th OR any Friday."""
        cron = CronExpr('0 12 13 * 5', tz='UTC')
        # 2026-08-13 is a Thursday -> matches on day-of-month.
        assert cron.last_occurrence(datetime(2026, 8, 13, 13, 0)) == \
            datetime(2026, 8, 13, 12, 0)
        # 2026-08-14 is a Friday -> matches on day-of-week.
        assert cron.last_occurrence(datetime(2026, 8, 14, 13, 0)) == \
            datetime(2026, 8, 14, 12, 0)
