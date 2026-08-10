"""``NotificationLedger`` — writes ``notification_log``, answers "did we already".

**Transaction discipline — the opposite of the route next to it.**
``webapp/dashboards/allocations/blueprint.py`` writes its activation event
*inside* ``management_transaction``, on purpose, because a decision that did
not take effect must not survive. A ledger row is the inverse: **mail handed
to a relay cannot be un-sent by a rollback**, so it must survive one. Every
method here therefore opens its own short-lived session and commits, exactly
as ``webapp/api/xras/replay.py`` does, and never enrols in whatever
transaction the caller is inside.

The two disciplines sit two screens apart in the webapp. A reader who has
just met one will expect the other answer, which is why both docstrings say
so.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 5.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import and_, func, or_, select

from sam.notify.base import Message, NotifyError, RenderedMessage
from sam.notify.config import NotifyConfig
from sam.notify.models import NotificationLog

logger = logging.getLogger(__name__)

#: Statuses that mean "this message reached, or may have reached, its
#: recipient" and therefore suppress a duplicate.
#:
#: ``queued`` is here because a process that died *after* handing the message
#: to the relay must not re-send — but see :meth:`NotificationLedger.already_sent`
#: for the horizon that keeps that from becoming permanent.
#:
#: ``failed`` is deliberately absent: a failure is exactly the case a retry is
#: for. ``suppressed`` is absent too, and that one is load-bearing — if a
#: suppression suppressed, the first skip would make every later attempt skip
#: for ever, on a key that had never actually been delivered.
SUPPRESSING_STATUSES = ('sent', 'redirected')


class LedgerError(NotifyError):
    """The ledger could not be read or written.

    Raised rather than swallowed on the *write before sending*, because the
    ledger is what makes a re-run safe. :class:`~sam.notify.service.Notifier`
    turns this into a ``failed`` result and **does not deliver**: an
    unrecorded send is one that the next run will send again.
    """


class NotificationLedger:
    """Records delivery attempts and answers the suppression question.

    Args:
        session_factory: a zero-argument callable returning a **new**
            ``Session``. Typically ``lambda: Session(db.engine)`` in the
            webapp and ``lambda: Session(engine)`` in the CLI. It is called
            once per operation and the session is closed straight after, so
            a ledger write never holds a connection across a send.
        config: supplies ``queued_stale_seconds``. Defaults to the
            environment, like :class:`~sam.notify.service.Notifier`'s.
    """

    def __init__(self, session_factory: Callable[[], object], *,
                 config: Optional[NotifyConfig] = None) -> None:
        self.session_factory = session_factory
        self.config = config or NotifyConfig.from_environment()

    # ----------------------------------------------------------------- write
    def record(self, message: Message, *, status: str, transport: str,
               detail: Optional[str] = None,
               rendered: Optional[RenderedMessage] = None) -> int:
        """Append one attempt and commit it. Returns the new row's id.

        Raises:
            LedgerError: on any database failure. See the class docstring for
                why this is not swallowed.
        """
        try:
            with self.session_factory() as session:
                row = NotificationLog.create(
                    session,
                    kind=message.kind,
                    channel=message.recipient.channel.value,
                    transport=transport,
                    status=status,
                    recipient=message.recipient.address,
                    intended_recipient=message.intended_recipient,
                    recipient_name=message.recipient.name,
                    recipient_role=message.recipient.role,
                    subject=message.subject,
                    template=rendered.template_text if rendered else None,
                    entity_type=message.entity_type,
                    entity_id=message.entity_id,
                    projcode=message.projcode,
                    dedup_key=message.dedup_key,
                    error=detail,
                    requested_by=message.requested_by,
                )
                log_id = row.notification_log_id
                session.commit()
                return log_id
        except Exception as exc:
            logger.exception('notify: could not write notification_log')
            raise LedgerError(str(exc)) from exc

    def resolve(self, log_id: int, *, status: str,
                detail: Optional[str] = None) -> None:
        """Close out a ``queued`` row with its outcome, and commit.

        ⚠️ **Swallows its own failures, deliberately.** By the time this runs
        the message is already with the relay and cannot be recalled; raising
        would turn a bookkeeping problem into a caller-visible send failure
        and, worse, invite a retry of a message that did go out. A row left
        ``queued`` is precisely what the staleness horizon in
        :meth:`already_sent` and the card's "Queued (stuck)" counter exist to
        surface.
        """
        try:
            with self.session_factory() as session:
                row = session.get(NotificationLog, log_id)
                if row is None:
                    logger.warning('notify: notification_log %s vanished '
                                   'before it could be resolved', log_id)
                    return
                row.resolve(status=status, error=detail)
                session.commit()
        except Exception:
            logger.exception(
                'notify: could not resolve notification_log %s to %r — the '
                'message was already handed to the transport; the row stays '
                'queued and will age past NOTIFY_QUEUED_STALE_SECONDS',
                log_id, status)

    # ------------------------------------------------------------------ read
    def already_sent(self, dedup_key: str, *,
                     since: Optional[datetime] = None) -> bool:
        """Has this key already been delivered (or is one in flight)?

        Args:
            dedup_key: the caller's key, built from the **intended**
                recipient — never the ``NOTIFY_REDIRECT_TO`` target, which
                would collapse a whole staging run onto one key.
            since: an optional lower bound on ``creation_time``.

        **``since=None`` — all time — is the correct default**, and the
        reason is worth stating because a 30-day window *looks* more
        conservative and is wrong. Both key formats already carry their own
        window::

            expiration       expiration:{projcode}:{latest_end_date}:{recipient}
            xras activation  xras_activation:{projcode}:{action_log_id}:{recipient}

        A new expiration date or a new XRAS action mints a *new key*, so
        nothing is suppressed for ever by accident. A time window layered on
        top would silently re-enable the re-email bug for anything older than
        it — which is the bug this whole table exists to fix.

        ⚠️ **A stale ``queued`` row must not suppress its own retry.**
        ``queued`` counts, because a process that died *after* handing the
        message to the relay must not re-send. But that is the same row a
        process that died *before* the relay leaves behind, and the two are
        indistinguishable. Left alone, one crash suppresses that recipient
        **permanently**, with ``--force`` the only recovery. So the ``queued``
        arm is qualified by ``creation_time > now - queued_stale_seconds``:
        fresh means "in flight, leave it", stale means "we never learned,
        try again".
        """
        if not dedup_key:
            return False

        horizon = datetime.now() - timedelta(
            seconds=self.config.queued_stale_seconds)

        conditions = [NotificationLog.dedup_key == dedup_key]
        if since is not None:
            conditions.append(NotificationLog.creation_time >= since)

        conditions.append(or_(
            NotificationLog.status.in_(SUPPRESSING_STATUSES),
            and_(NotificationLog.status == 'queued',
                 NotificationLog.creation_time > horizon),
        ))

        try:
            with self.session_factory() as session:
                found = session.execute(
                    select(NotificationLog.notification_log_id)
                    .where(*conditions)
                    .limit(1)
                ).first()
                return found is not None
        except Exception as exc:
            # Fail OPEN on a read failure, unlike the write path. A ledger
            # that cannot be queried must not become a mailer that cannot
            # send — the write in :meth:`record` still fails closed, so an
            # unrecordable send is still refused.
            logger.warning('notify: suppression query failed (%s); '
                           'not suppressing', exc)
            return False

    def stuck_queued(self, *, since: Optional[datetime] = None) -> int:
        """How many rows are ``queued`` past the staleness horizon.

        The same predicate as the ``queued`` arm of :meth:`already_sent`,
        inverted — which is the point: the counter an operator reads on the
        admin card and the rule that lets a retry through are one mechanism,
        not two that can disagree.
        """
        horizon = datetime.now() - timedelta(
            seconds=self.config.queued_stale_seconds)
        conditions = [NotificationLog.status == 'queued',
                      NotificationLog.creation_time <= horizon]
        if since is not None:
            conditions.append(NotificationLog.creation_time >= since)

        with self.session_factory() as session:
            return session.execute(
                select(func.count(NotificationLog.notification_log_id))
                .where(*conditions)
            ).scalar_one()
