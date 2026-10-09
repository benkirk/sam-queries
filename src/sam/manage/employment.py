"""Match a user's incoming affiliations (IdM positions/collaborations) to SAM's rows.

Pure: no session. The rungs are legacy ``UserEmploymentSynchronizer``'s, minus its
defects (``docs/plans/LDAP_SYNC_API.md`` D1-D4): an unknown or foreign id falls
through to the ladder instead of a 500, an id must also name the same employer,
each SAM row matches at most one incoming record, and an inverted range simply
does not overlap. Rows absent from the payload are never ended or deleted.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Affiliation:
    """One incoming record, or one SAM row reduced to the fields the ladder reads."""
    employment_id: Optional[int]
    employer_id: Optional[int]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    idms_unique_name: Optional[str] = None


def _day(value: Optional[datetime], default: date) -> date:
    return value.date() if value is not None else default


def overlaps(a: Affiliation, b: Affiliation) -> bool:
    """Day-granular overlap; an open end runs forever; an inverted range overlaps nothing."""
    a0, a1 = _day(a.start_date, date.min), _day(a.end_date, date.max)
    b0, b1 = _day(b.start_date, date.min), _day(b.end_date, date.max)
    if a0 > a1 or b0 > b1:
        return False
    return a0 <= b1 and b0 <= a1


def _same_data(a: Affiliation, b: Affiliation) -> bool:
    return (a.start_date, a.end_date, a.idms_unique_name) == (
        b.start_date, b.end_date, b.idms_unique_name)


def match(incoming: list, existing: list, *, label: str = 'affiliation') -> list:
    """``[(incoming, existing_or_None), ...]`` in incoming order; None means insert."""
    by_id = {row.employment_id: row for row in existing}
    consumed = set()
    out = []
    for rec in incoming:
        found = None
        if rec.employment_id is not None:
            row = by_id.get(rec.employment_id)
            if row is not None and row.employer_id == rec.employer_id \
                    and row.employment_id not in consumed:
                found = row
            else:
                logger.info('ldapsync: %s id %s is not this user\'s row for employer %s; matching '
                            'by data instead', label, rec.employment_id, rec.employer_id)
        if found is None:
            candidates = [r for r in existing if r.employer_id == rec.employer_id
                          and r.employment_id not in consumed]
            rungs = (
                lambda r: _same_data(r, rec),
                lambda r: rec.idms_unique_name is not None
                and r.idms_unique_name == rec.idms_unique_name,
                lambda r: (r.start_date, r.end_date) == (rec.start_date, rec.end_date),
                lambda r: overlaps(r, rec),
            )
            for rung in rungs:
                found = next((r for r in candidates if rung(r)), None)
                if found is not None:
                    break
        if found is not None:
            consumed.add(found.employment_id)
        out.append((rec, found))
    return out
