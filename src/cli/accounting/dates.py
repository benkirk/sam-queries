"""
Shared date-range helpers for accounting CLI commands.

Used by both sam-admin accounting (admin.py) and sam-search accounting (search.py).
"""
import click
from datetime import date, datetime, timedelta


def _parse_last_spec(spec: str) -> int:
    """Parse --last spec: '3d' or '3' → 3."""
    s = spec.strip().lower().rstrip('d')
    try:
        n = int(s)
    except ValueError:
        raise click.BadParameter(f"--last must be Nd or N (e.g. 3d), got: {spec!r}")
    if n < 1:
        raise click.BadParameter("--last N must be >= 1")
    return n


def _validate_accounting_dates(
    date_str: str | None,
    start: str | None,
    end: str | None,
    today_flag: bool,
    last: str | None,
) -> None:
    if today_flag and (date_str or start or end or last):
        raise click.BadParameter("--today cannot be combined with --date, --start, --end, or --last")
    if last and (date_str or start or end or today_flag):
        raise click.BadParameter("--last cannot be combined with --date, --start, --end, or --today")
    if date_str and (start or end):
        raise click.BadParameter("Cannot use --date with --start/--end")
    if not any([date_str, start, end, today_flag, last]):
        raise click.UsageError("Specify a date: --date, --today, --last N[d], or --start/--end")
    for val, name in [(date_str, '--date'), (start, '--start'), (end, '--end')]:
        if val:
            try:
                datetime.strptime(val, '%Y-%m-%d')
            except ValueError:
                raise click.BadParameter(f"{name} must be in YYYY-MM-DD format")


def _resolve_accounting_dates(
    date_str: str | None,
    start: str | None,
    end: str | None,
    today_flag: bool,
    last: str | None,
) -> tuple[date, date]:
    today = date.today()
    if today_flag:
        return today, today
    if last:
        n = _parse_last_spec(last)
        return today - timedelta(days=n - 1), today
    if date_str:
        d = date.fromisoformat(date_str)
        return d, d
    # --start / --end: match jobhist-sync defaults for missing bound
    yesterday = today - timedelta(days=1)
    s = date.fromisoformat(start) if start else date.fromisoformat('2024-01-01')
    e = date.fromisoformat(end) if end else yesterday
    return s, e
