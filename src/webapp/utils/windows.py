"""Date windows read off a request's query string.

Three shapes, one home. ``read_days`` is a clamped lookback. ``read_log_window``
is an audit log's ``start_date`` / ``end_date``: a default lookback on first
load, and whatever was typed after that. ``read_chart_window`` is a chart's:
always bounded on both sides, and a malformed date is an error.
"""

from datetime import datetime, timedelta

from sam.dates import parse_ymd, parse_ymd_end_of_day, parse_ymd_or, start_of_today


def read_days(args, *, default, maximum, name='days'):
    """``?days=`` clamped to ``[1, maximum]``; missing or junk reads ``default``."""
    days = args.get(name, type=int) or default
    return max(1, min(days, maximum))


def read_log_window(args, default_days):
    """``(start, end)`` for an audit log; either may be ``None`` (unbounded).

    The default lookback applies only when NEITHER param is in the query
    string. A blank one was cleared on purpose and means all time; a malformed
    one reads as blank.

    WARNING: the default has no upper bound. MySQL DATETIME rounds to the
    second, so a row written at 10:10:24.894 is stored as 10:10:25 and falls
    after an ``end`` of ``now()`` taken in the same request: the newest row,
    the one an operator checking "was my action recorded?" came to see.
    """
    if 'start_date' not in args and 'end_date' not in args:
        return start_of_today() - timedelta(days=default_days), None
    return (parse_ymd_or((args.get('start_date') or '').strip()),
            parse_ymd_or((args.get('end_date') or '').strip(), at_end_of_day=True))


def read_chart_window(args, default_days):
    """``(start, end)``, both always set; ``ValueError`` on a malformed date."""
    start_raw, end_raw = args.get('start_date'), args.get('end_date')
    now = datetime.now()
    return (parse_ymd(start_raw) if start_raw else now - timedelta(days=default_days),
            parse_ymd_end_of_day(end_raw) if end_raw else now)
