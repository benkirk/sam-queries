"""Stdlib-only date parsing for form, query-string and wire input.

Not in ``sam.fmt``: that module imports ``config``, which the webapp boot order cannot take here.
"""
from datetime import datetime

YMD = '%Y-%m-%d'


def parse_ymd(s: str) -> datetime:
    """``YYYY-MM-DD`` as midnight; ``ValueError`` on a malformed string."""
    return datetime.strptime(s, YMD)


def parse_ymd_end_of_day(s: str) -> datetime:
    """``YYYY-MM-DD`` as 23:59:59, the stored end-date convention."""
    return parse_ymd(s).replace(hour=23, minute=59, second=59)


def parse_ymd_or(s, default=None, end_of_day=False):
    """``parse_ymd`` (or ``parse_ymd_end_of_day``), else ``default`` for a missing or malformed string."""
    try:
        return (parse_ymd_end_of_day if end_of_day else parse_ymd)(s) if s else default
    except ValueError:
        return default


def start_of_today() -> datetime:
    """Midnight today, naive local like the database."""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
