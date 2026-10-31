"""Who an allocation type is for: the audience registry, and the audiences a user belongs to.

A guide row (``allocation_type_guide.audience``) names at most one audience, the one a type is
specific to; NULL is everyone. Breadth lives on the user's side: ``user_audiences`` returns every
audience whose rule applies, so a type for one audience never needs a list. A facility that joins
later (Wyoming, say: the current institution's state is Wyoming) is one more row here plus its
guide rows. ``public`` marks the audiences an anonymous visitor's page shows. Flask-free.
Design: ``docs/plans/XRAS_SUBMISSION_SPRINT3.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional


def is_ncar_staff(user) -> bool:
    """Legacy's internal rule: a current organization and no current institution."""
    from sam.xras.extractors import best_institution, best_organization
    return best_organization(user) is not None and best_institution(user) is None


@dataclass(frozen=True)
class Audience:
    key: str
    label: str
    applies: Callable[[object], bool]
    public: bool = True


UNIVERSITY = 'university'
NCAR = 'ncar'

#: The registry, in display order. A key fits ``allocation_type_guide.audience`` (16 chars).
AUDIENCES = (
    Audience(UNIVERSITY, 'University', lambda user: not is_ncar_staff(user)),
    Audience(NCAR, 'NCAR staff', is_ncar_staff, public=False),
)

AUDIENCE_LABELS = {a.key: a.label for a in AUDIENCES}
#: The audiences a visitor with no SAM account is shown.
VISITOR_AUDIENCES: FrozenSet[str] = frozenset(a.key for a in AUDIENCES if a.public)


def user_audiences(user: Optional[object]) -> FrozenSet[str]:
    """Every audience whose rule applies to *user*; ``university`` when none does or no user."""
    found = frozenset(a.key for a in AUDIENCES if user is not None and a.applies(user))
    return found or frozenset({UNIVERSITY})
