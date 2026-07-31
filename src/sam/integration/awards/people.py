"""Map a funding agency's person onto a SAM ``User``.

This is the *only* place an external :class:`~sam.integration.awards.base.PersonRef`
becomes a SAM row, and that is deliberate. Today
``contract.principal_investigator_user_id`` and ``contract_monitor_user_id``
are both FKs into ``users``, so every NSF program director is a row in
UCAR's identity table — 314 of the 387 distinct Monitors exist for no other
reason. docs/plans/CONTRACT_IMPORTING_PLAN.md § F2 argues for a lightweight
``external_contact`` table instead; keeping the mapping in one function means
that change is a new return type here plus two FK columns, not a rewrite of
the prefill path.

Matching is **suggest, don't impose**: a miss returns ``None`` and the caller
renders the agency's raw name/email as a hint. We never create a ``User``.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func

from sam.core.users import EmailAddress, User
from sam.integration.awards.base import PersonRef

logger = logging.getLogger(__name__)


def resolve_person(session, person: Optional[PersonRef]) -> Optional[User]:
    """Return the SAM user *person* refers to, or ``None``.

    Email first (exact, case-insensitive), then a first+last name match.
    """
    if not person:
        return None

    user = _by_email(session, person.email)
    if user is not None:
        return user
    return _by_name(session, person.name)


def _by_email(session, email: Optional[str]) -> Optional[User]:
    """Case-insensitive match across **every** ``email_address`` row.

    Not filtered on ``is_primary``: it is unset for many of exactly these
    users (external contacts entered by hand), and filtering on it during
    research produced a false 0-of-12 match rate.
    """
    if not email or '@' not in email:
        return None
    return (
        session.query(User)
        .join(EmailAddress, EmailAddress.user_id == User.user_id)
        .filter(func.lower(EmailAddress.email_address) == email.strip().lower())
        .order_by(User.user_id)
        .first()
    )


def _by_name(session, name: Optional[str]) -> Optional[User]:
    """Match ``first last`` against ``users``, only when it is unambiguous.

    Agency names arrive as free text ("Carrie E. Black", "Eric J Barron"),
    so the middle token is dropped and only the first and last are used.
    Two or more hits means we cannot tell them apart — return ``None`` and
    let the operator pick, rather than guess.
    """
    parts = (name or '').replace('.', ' ').split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]

    matches = (
        session.query(User)
        .filter(func.lower(User.first_name) == first.lower(),
                func.lower(User.last_name) == last.lower())
        .order_by(User.user_id)
        .limit(2)
        .all()
    )
    if len(matches) == 1:
        return matches[0]
    if matches:
        logger.debug('award people: %r is ambiguous (%d matches)', name,
                     len(matches))
    return None
