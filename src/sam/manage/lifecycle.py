"""Finishing an identity-sync deactivation, and undoing one.

``PUT ldapsync/user`` with IdM inactive only stamps ``users.deactivate``; the user
stays active ("pending") for a grace period. ``finish_user_deactivation`` then
closes every live membership at **one shared instant** and sets ``active=0``.

That instant is the undo's record, with no table behind it: the newest instant at
which a user's memberships all closed together is the deactivation, and
``restore_user_deactivation`` reopens the rows still carrying it. Legacy closures
carry the same signature, so this also repairs deactivations from before this
code (``scripts/repair/RUNBOOK-missing-projects.md``). Restored rows come back
open-ended; a prior end date is not recorded (6 of 42,073 live rows had one).
Design: ``docs/plans/LDAP_SYNC_API.md`` P2.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import select

from sam.accounting.accounts import Account, AccountUser
from sam.core.users import User
from sam.manage import _end_membership
from sam.manage.ldapsync import SyncValidationError, user_by_username

logger = logging.getLogger(__name__)

RESTORED = 'restored'
SKIPPED_ACCOUNT_GONE = 'skipped_account_gone'     # the account was deleted since
END_OF_DAY = time(23, 59, 59)


def pending_deactivations(session, hours: int, now: Optional[datetime] = None) -> list:
    """Usernames stamped for deactivation more than *hours* ago and still active."""
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    return list(session.scalars(
        select(User.username)
        .where(User.active.is_(True), User.deactivate.is_not(None), User.deactivate < cutoff)
        .order_by(User.username)))


def finish_user_deactivation(session, username: str, now: Optional[datetime] = None) -> int:
    """Close every live membership at one instant, then ``active=0``; returns rows closed."""
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
    user.update(active=False, deactivate=None)
    logger.info('ldapsync: finished deactivation of %s, %d memberships closed',
                user.username, len(live))
    return len(live)


@dataclass
class Closure:
    """The instant a deactivation closed all of a user's memberships, and those rows."""
    instant: datetime
    rows: list = field(default_factory=list)


def find_deactivation_closure(session, user: User,
                              now: Optional[datetime] = None) -> Optional[Closure]:
    """The newest instant that closed every membership live just before it, or None.

    An instant older than the start of any membership open now is not the user's
    last state (they were re-added, or already restored), so it is never offered.
    Residual risk: removing a user from their only project looks the same.
    """
    now = now or datetime.now()
    rows = session.query(AccountUser).filter(AccountUser.user_id == user.user_id).all()
    open_starts = [r.start_date for r in rows if r.start_date <= now
                   and (r.end_date is None or r.end_date >= now)]
    newest_open_start = max(open_starts, default=None)
    # A 23:59:59 end is a scheduled one (the end-of-day convention), never a closure.
    ended = {r.end_date for r in rows if r.end_date is not None and r.end_date < now
             and r.end_date.time() != END_OF_DAY}
    for instant in sorted(ended, reverse=True):
        if newest_open_start is not None and newest_open_start > instant:
            return None
        closed = {r.account_user_id for r in rows if r.end_date == instant}
        live_before = {r.account_user_id for r in rows
                       if r.start_date <= instant
                       and (r.end_date is None or r.end_date >= instant)}
        if closed == live_before:
            return Closure(instant, sorted((r for r in rows if r.end_date == instant),
                                           key=lambda r: r.account_user_id))
    return None


@dataclass
class RestoreReport:
    username: str
    instant: Optional[datetime]
    outcomes: list = field(default_factory=list)    # (AccountUser, outcome)

    @property
    def restored(self) -> int:
        return sum(1 for _, outcome in self.outcomes if outcome == RESTORED)


def restore_user_deactivation(session, user: User, now: Optional[datetime] = None, *,
                              dry_run: bool = False,
                              within_days: Optional[int] = None) -> RestoreReport:
    """Reopen the memberships the user's last deactivation closed; idempotent.

    ``within_days`` bounds the automatic path: a closure older than that is a person
    returning after a real departure, not an accidental deactivation, and is left alone.
    """
    now = now or datetime.now()
    closure = find_deactivation_closure(session, user, now)
    if closure is not None and within_days is not None \
            and closure.instant < now - timedelta(days=within_days):
        logger.info('ldapsync: %s closure at %s is older than %d days; not restored',
                    user.username, closure.instant, within_days)
        closure = None
    report = RestoreReport(user.username, closure.instant if closure else None)
    if closure is None:
        return report
    for row in closure.rows:
        # A second open row on the account cannot occur here: it would have been live
        # at the instant (no closure) or started after it (no closure either).
        account = session.get(Account, row.account_id)
        if account is None or account.deleted:
            outcome = SKIPPED_ACCOUNT_GONE
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
