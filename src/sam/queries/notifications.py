"""Read-side queries over ``notification_log``.

Built as a shared query layer even though only the webapp consumes it —
`sam-admin` has no notifications command and that is deliberate (§ 8). The
door stays open, and the counts the admin card renders are computed here
rather than in a route, so a future CLI cannot drift from the page.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 8.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from sam.notify.models import NotificationLog

#: The status values the admin card renders as named rows, in card order.
#: A status outside this list still appears in ``by_status`` — the card just
#: has no dedicated row for it, which is the right failure mode for a
#: vocabulary that can grow.
CARD_STATUSES = ('sent', 'redirected', 'failed', 'suppressed')

#: How far back the card's headline counts look.
DEFAULT_WINDOW_HOURS = 24


def summarize_notifications(session: Session, *,
                            since: Optional[datetime] = None,
                            window_hours: int = DEFAULT_WINDOW_HOURS,
                            queued_stale_seconds: int = 300) -> Dict[str, Any]:
    """Counts for the Admin → Configuration card.

    **One grouped query for the statuses**, plus one scalar for the stuck
    counter — not one query per row on the card.

    Args:
        since: explicit lower bound. Overrides ``window_hours``.
        window_hours: how far back to count when ``since`` is not given.
        queued_stale_seconds: must match
            ``NotifyConfig.queued_stale_seconds``. The "stuck" counter and
            the rule that lets a suppressed retry through are **one
            mechanism** (see :meth:`sam.notify.ledger.NotificationLedger.already_sent`);
            passing a different horizon here would make the card disagree
            with the mailer about what "stuck" means.

    Returns:
        ``{'window_start', 'by_status', 'total', 'queued_stuck', <status>: n}``
        with every :data:`CARD_STATUSES` key present and zeroed, so the
        template needs no ``default(0)`` on any of them.
    """
    window_start = since or (datetime.now() - timedelta(hours=window_hours))

    rows = session.execute(
        select(NotificationLog.status,
               func.count(NotificationLog.notification_log_id))
        .where(NotificationLog.creation_time >= window_start)
        .group_by(NotificationLog.status)
    ).all()

    by_status = {status: count for status, count in rows}

    summary: Dict[str, Any] = {
        'window_start': window_start,
        'window_hours': window_hours,
        'by_status': by_status,
        'total': sum(by_status.values()),
        # Deliberately NOT windowed: a row stuck three days ago is more
        # interesting than one stuck an hour ago, and windowing it would let
        # the oldest breakage quietly age off the card.
        'queued_stuck': count_stuck_queued(
            session, queued_stale_seconds=queued_stale_seconds),
    }
    for status in CARD_STATUSES:
        summary[status] = by_status.get(status, 0)
    return summary


def count_stuck_queued(session: Session, *,
                       queued_stale_seconds: int = 300) -> int:
    """Rows still ``queued`` past the staleness horizon.

    Non-zero means a process died mid-send: it wrote ``queued`` and never
    wrote the outcome. Those rows stop suppressing their own retry at exactly
    this horizon, so this counter is how an operator learns the crash
    happened at all.
    """
    horizon = datetime.now() - timedelta(seconds=queued_stale_seconds)
    return session.execute(
        select(func.count(NotificationLog.notification_log_id))
        .where(NotificationLog.status == 'queued',
               NotificationLog.creation_time <= horizon)
    ).scalar_one()


def get_recent_notifications(session: Session, *,
                             since: Optional[datetime] = None,
                             statuses: Optional[Sequence[str]] = None,
                             kinds: Optional[Sequence[str]] = None,
                             channels: Optional[Sequence[str]] = None,
                             search: Optional[str] = None,
                             limit: int = 100,
                             offset: int = 0) -> List[NotificationLog]:
    """The activity-log table's rows, newest first.

    ``search`` is a substring match on recipient **or** projcode — the two
    things an operator arrives knowing. It is `ilike` deliberately: the
    identifier columns are utf8mb3_general_ci here so case does not matter,
    but the CLI's contract search learned the hard way that assuming a
    collation is how you undercount (see the `reference` note on
    case-sensitive contract columns), and being explicit costs nothing.
    """
    query = select(NotificationLog).where(*_filters(
        since=since, statuses=statuses, kinds=kinds, channels=channels,
        search=search))
    return list(session.execute(
        query.order_by(NotificationLog.creation_time.desc(),
                       NotificationLog.notification_log_id.desc())
        .limit(limit).offset(offset)
    ).scalars())


def count_recent_notifications(session: Session, **filters) -> int:
    """Total matching rows, for pagination."""
    return session.execute(
        select(func.count(NotificationLog.notification_log_id))
        .where(*_filters(**filters))
    ).scalar_one()


def facet_notifications(session: Session, dimension: str,
                        **filters) -> Dict[str, int]:
    """Counts for one facet dimension, **excluding that dimension's filter**.

    ⚠️ Self-exclusion is the whole point, and the same discipline
    ``xras_fragment`` keeps. Scope a dimension by itself and every unselected
    value drops to zero the moment one is picked — the chips stop being
    switchers and become dead ends. So asking for the ``status`` facet drops
    the ``statuses`` filter while keeping ``kinds``, ``channels`` and the
    rest.

    Args:
        dimension: ``'status'`` / ``'kind'`` / ``'channel'``.
    """
    column = {
        'status': NotificationLog.status,
        'kind': NotificationLog.kind,
        'channel': NotificationLog.channel,
    }.get(dimension)
    if column is None:
        raise ValueError(
            f'unknown facet dimension {dimension!r}; expected one of '
            f'status, kind, channel')

    own_filter = {'status': 'statuses', 'kind': 'kinds',
                  'channel': 'channels'}[dimension]
    scoped = {k: v for k, v in filters.items() if k != own_filter}

    rows = session.execute(
        select(column, func.count(NotificationLog.notification_log_id))
        .where(*_filters(**scoped))
        .group_by(column)
        .order_by(column)
    ).all()
    return {value: count for value, count in rows}


def _filters(*, since: Optional[datetime] = None,
             statuses: Optional[Sequence[str]] = None,
             kinds: Optional[Sequence[str]] = None,
             channels: Optional[Sequence[str]] = None,
             search: Optional[str] = None) -> list:
    """The WHERE terms shared by the table, the count and the facets.

    One builder so a filter added to the table cannot be forgotten in the
    facet rollups — which would show counts the table does not honour.
    """
    conditions = []
    if since is not None:
        conditions.append(NotificationLog.creation_time >= since)
    if statuses:
        conditions.append(NotificationLog.status.in_(list(statuses)))
    if kinds:
        conditions.append(NotificationLog.kind.in_(list(kinds)))
    if channels:
        conditions.append(NotificationLog.channel.in_(list(channels)))
    if search:
        term = f'%{search.strip()}%'
        conditions.append(or_(
            NotificationLog.recipient.ilike(term),
            NotificationLog.projcode.ilike(term),
        ))
    return conditions
