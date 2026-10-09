"""Finishing an identity-sync deactivation, and undoing one.

``PUT ldapsync/user`` with IdM inactive only stamps ``users.deactivate``; the user
stays active ("pending") for a grace period. ``finish_user_deactivation`` then
closes every live membership at **one shared instant**, sets ``active=0`` and keeps
that instant in ``users.deactivate``: the column is the ledger, no table behind it.
``restore_user_deactivation`` reopens the rows whose ``end_date`` equals it. A user
deactivated before this code (``active=0``, stamp NULL) is not undone; the repair
for that cohort stays ``scripts/repair/RUNBOOK-missing-projects.md``. Restored rows
come back open-ended. Design: ``docs/plans/LDAP_SYNC_API.md`` § 10.4.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from sam.accounting.accounts import Account, AccountUser
from sam.core.users import User
from sam.manage import _end_membership, membership_cutoff
from sam.manage.ldapsync import SyncValidationError, user_by_username

logger = logging.getLogger(__name__)

RESTORED = 'restored'
SKIPPED_ACCOUNT_GONE = 'skipped_account_gone'       # the account was deleted since
SKIPPED_ALREADY_MEMBER = 'skipped_already_member'   # re-added since; reopening would duplicate


def pending_deactivations(session, hours: int, now: Optional[datetime] = None) -> list:
    """Usernames stamped for deactivation more than *hours* ago and still active."""
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    return list(session.scalars(
        select(User.username)
        .where(User.active.is_(True), User.deactivate.is_not(None), User.deactivate < cutoff)
        .order_by(User.username)))


def finish_user_deactivation(session, username: str, now: Optional[datetime] = None) -> int:
    """Close every live membership at one instant, keep it as the stamp, ``active=0``; returns rows closed."""
    now = now or datetime.now()
    user = user_by_username(session, username)
    if user is None or user.deactivate is None or not user.active:
        raise SyncValidationError(f'User {username} is not active and marked for deactivation.')
    live = (session.query(AccountUser)
            .filter(AccountUser.user_id == user.user_id, AccountUser.start_date <= now,
                    (AccountUser.end_date.is_(None)) | (AccountUser.end_date >= now))
            .all())
    for row in live:
        _end_membership(session, row, now)      # one `now`, so one stamp for every row
    user.update(active=False, deactivate=membership_cutoff(now))
    logger.info('ldapsync: finished deactivation of %s, %d memberships closed',
                user.username, len(live))
    return len(live)


@dataclass
class Closure:
    """The instant a finished deactivation closed the user's memberships, and those rows."""
    instant: datetime
    rows: list = field(default_factory=list)


def find_deactivation_closure(session, user: User,
                              closed_at: Optional[datetime] = None) -> Optional[Closure]:
    """The rows a finished deactivation closed, or None (pending, never finished, or already restored)."""
    closed_at = closed_at or (user.deactivate if not user.active else None)
    if closed_at is None:
        return None
    rows = (session.query(AccountUser)
            .filter(AccountUser.user_id == user.user_id, AccountUser.end_date == closed_at)
            .order_by(AccountUser.account_user_id).all())
    return Closure(closed_at, rows) if rows else None


@dataclass
class RestoreReport:
    username: str
    instant: Optional[datetime]
    outcomes: list = field(default_factory=list)    # (AccountUser, outcome)

    @property
    def restored(self) -> int:
        return sum(1 for _, outcome in self.outcomes if outcome == RESTORED)


def restore_user_deactivation(session, user: User, now: Optional[datetime] = None, *,
                              closed_at: Optional[datetime] = None,
                              dry_run: bool = False,
                              within_days: Optional[int] = None) -> RestoreReport:
    """Reopen the memberships the user's finished deactivation closed; idempotent.

    ``closed_at`` is the stamp when the caller has already cleared it (the IdM
    reactivation hook). ``within_days`` bounds the automatic path: an older closure
    is a person returning after a real departure and is left alone.
    """
    now = now or datetime.now()
    closure = find_deactivation_closure(session, user, closed_at)
    if closure is not None and within_days is not None \
            and closure.instant < now - timedelta(days=within_days):
        logger.info('ldapsync: %s closure at %s is older than %d days; not restored',
                    user.username, closure.instant, within_days)
        closure = None
    report = RestoreReport(user.username, closure.instant if closure else None)
    if closure is None:
        return report
    open_accounts = set(session.scalars(
        select(AccountUser.account_id)
        .where(AccountUser.user_id == user.user_id, AccountUser.start_date <= now,
               (AccountUser.end_date.is_(None)) | (AccountUser.end_date >= now))))
    for row in closure.rows:
        account = session.get(Account, row.account_id)
        if account is None or account.deleted:
            outcome = SKIPPED_ACCOUNT_GONE
        elif row.account_id in open_accounts:
            outcome = SKIPPED_ALREADY_MEMBER
        else:
            outcome = RESTORED
            if not dry_run:
                row.end_date = None
        report.outcomes.append((row, outcome))
    if not dry_run:
        session.flush()
        logger.info('ldapsync: restored %d of %d memberships of %s closed at %s',
                    report.restored, len(report.outcomes), user.username, closure.instant)
    return report
