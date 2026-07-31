"""Cell formatters shared by the contract and award display modules.

Both packages render the same payloads — `ContractSummarySchema` output and
`compare_contract` results — so they need the same three coercions. They grew
a private copy each (#403 then #404) and the copies drifted: one `_date` was
missing the `date`/`datetime` guard, and an empty string rendered as `—` in
award output but as `''` in contract output. These are the more-correct
versions of each.

All date formatting still goes through `sam.fmt` rather than a local
`strftime` or a string slice, per the house rule.
"""

from datetime import date, datetime

from sam import fmt

#: What every formatter here renders for "nothing to show". Matches
#: `sam.fmt`'s own default null marker.
BLANK = '—'


def text(value) -> str:
    """A value for a table cell, with `None` and `''` both reading as blank."""
    return BLANK if value is None or value == '' else str(value)


def truncate(value, width: int = 48) -> str:
    """Keep titles from wrapping the table into unreadability."""
    if not value:
        return BLANK
    value = str(value)
    return value if len(value) <= width else value[:width - 1] + '…'


def date_cell(value) -> str:
    """Format a date that may have arrived as an ISO string.

    `build_award` keeps real `date` objects (`_SAMEncoder` serialises them),
    but anything embedding `ContractSummarySchema` output gets ISO text
    instead — correct for the JSON payload, wrong for `fmt.date_str`, which
    wants an object. Parse, then hand off.

    A string that does not parse is returned as-is rather than raising: it is
    a display path, and showing the raw value beats a traceback.
    """
    if not value:
        return BLANK
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, (date, datetime)):
        return fmt.date_str(value)
    return str(value)
