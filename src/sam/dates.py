"""Stdlib-only date parsing for form, query-string and wire input.

Not in ``sam.fmt``: that module imports ``config``, which the webapp boot order cannot take here.
"""
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

YMD = '%Y-%m-%d'

#: SAM's naive datetimes are Mountain; legacy's JVM ran in this zone, so its epoch
#: milliseconds are Mountain wall time. A fixed offset would drift an hour across DST.
SERVER_TZ = ZoneInfo('America/Denver')


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


def parse_wire_date(value: Any) -> Optional[date]:
    """A ``date``, ``datetime``, ``YYYY-MM-DD`` or ISO timestamp as a ``date``; ``None`` when missing or malformed."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return parse_ymd(str(value)[:10]).date() if value else None
    except ValueError:
        return None


def to_epoch_millis(value) -> Optional[int]:
    """A naive-Mountain ``datetime`` (a ``date`` at local midnight) as epoch milliseconds."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    return int(value.replace(tzinfo=SERVER_TZ).timestamp() * 1000)


def from_epoch_millis(ms) -> datetime:
    """Epoch milliseconds as a naive-Mountain ``datetime``, truncated to the second."""
    utc = datetime.fromtimestamp(int(ms) // 1000, tz=timezone.utc)
    return utc.astimezone(SERVER_TZ).replace(tzinfo=None)
