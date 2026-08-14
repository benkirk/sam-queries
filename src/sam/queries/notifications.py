"""Read-side queries over ``notification_log``.

Built as a shared query layer even though only the webapp consumes it —
`sam-admin` has no notifications command and that is deliberate (§ 8). The
door stays open, and the counts the admin card renders are computed here
rather than in a route, so a future CLI cannot drift from the page.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 8.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from querykit import LogSpec, count_rows, facet_counts, page_rows
from sam.notify.models import NotificationLog

#: The status values the admin card renders as named rows, in card order.
#: A status outside this list still appears in ``by_status`` — the card just
#: has no dedicated row for it, which is the right failure mode for a
#: vocabulary that can grow.
CARD_STATUSES = ('sent', 'redirected', 'failed', 'suppressed')

#: How far back the card's headline counts look.
DEFAULT_WINDOW_HOURS = 24

#: Statuses that mean a person received the message. `redirected` counts —
#: it reached *a* mailbox, which is what a staging run is for — and matches
#: `_DELIVERED_STATUSES` in ``sam.queries.xras_activation``, deliberately:
#: two "was this delivered" answers that disagreed would put a green badge on
#: one card and a grey one on another for the same row.
DELIVERED_STATUSES = ('sent', 'redirected')


def get_expiration_notice_status(
        session: Session,
        projcodes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """When each project was last told its allocation is expiring.

    One indexed fetch for every expiration notice about these projects,
    bucketed in Python — the shape
    :func:`sam.queries.xras_activation.get_xras_activity` already uses, and
    for the same reason: ``limit=None`` is safe *because* the projcode ``IN``
    list bounds the result, and `notification_log_projcode` is
    ``(projcode, creation_time)`` so the equality form can use it.

    Args:
        projcodes: the projects on the page. Empty returns ``{}`` without a
            query.

    Returns:
        An entry for **every** requested projcode, including those never
        notified (``notified: False``). That is deliberate: the consumer is a
        template macro shared with the user dashboard, and it must be able to
        tell "notified", "not notified" and "nobody asked" apart. Making
        absence mean only the third leaves the first two to ``notified``,
        rather than overloading a missing key with two meanings.

        ``notified_age`` is a **timedelta**, because ``fmt.ago`` takes an
        elapsed delta — and keeping ``datetime.now()`` out of Jinja is what
        makes the whole thing testable.
    """
    if not projcodes:
        return {}

    by_projcode: Dict[str, List[NotificationLog]] = {}
    for row in get_recent_notifications(session, projcodes=list(projcodes),
                                        kinds=['expiration'], limit=None):
        by_projcode.setdefault(row.projcode, []).append(row)

    # ONE `now` for the whole page, so two cards rendered from the same
    # request cannot report ages a second apart.
    now = datetime.now()
    result: Dict[str, Dict[str, Any]] = {}
    for projcode in projcodes:
        rows = by_projcode.get(projcode, [])
        # `SPEC.order_columns` is creation_time DESC, so the first delivered
        # row is the newest one.
        delivered = [r for r in rows if r.status in DELIVERED_STATUSES]
        newest = delivered[0] if delivered else None
        result[projcode] = {
            'notified': bool(delivered),
            'notified_time': newest.creation_time if newest else None,
            'notified_age': (now - newest.creation_time) if newest else None,
            'delivered_count': len(delivered),
            'failed_count': sum(1 for r in rows if r.status == 'failed'),
        }
    return result


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
                             projcodes: Optional[Sequence[str]] = None,
                             limit: Optional[int] = 100,
                             offset: int = 0) -> List[NotificationLog]:
    """The activity-log table's rows, newest first.

    ``search`` is a substring match on recipient **or** projcode — the two
    things an operator arrives knowing. It is `ilike` deliberately: the
    identifier columns are utf8mb3_general_ci here so case does not matter,
    but the CLI's contract search learned the hard way that assuming a
    collation is how you undercount (see the `reference` note on
    case-sensitive contract columns), and being explicit costs nothing.

    ``projcodes`` is the **equality** form and exists because ``search`` is
    not a substitute for it: ``search`` compiles to a leading-wildcard
    ``LIKE`` ORed against ``recipient``, which cannot use
    ``notification_log_projcode (projcode, creation_time)``. A caller that
    knows exactly which projects it wants — the XRAS activity table asking
    "what was sent about these forty" — must use this one.

    ``limit=None`` means no cap. That is for a caller that has already
    bounded the result some other way (a projcode ``IN`` list and a time
    window); a paginated table must always pass a number.
    """
    return page_rows(session, SPEC, limit=limit, offset=offset,
                     since=since, statuses=statuses, kinds=kinds,
                     channels=channels, search=search, projcodes=projcodes)


def count_recent_notifications(session: Session, **filters) -> int:
    """Total matching rows, for pagination."""
    return count_rows(session, SPEC, **filters)


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
    return facet_counts(session, SPEC, dimension, **filters)


def _filters(*, since: Optional[datetime] = None,
             statuses: Optional[Sequence[str]] = None,
             kinds: Optional[Sequence[str]] = None,
             channels: Optional[Sequence[str]] = None,
             search: Optional[str] = None,
             projcodes: Optional[Sequence[str]] = None) -> list:
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
    if projcodes:
        # Equality, not `search` — this is the term that can use
        # notification_log_projcode. See get_recent_notifications.
        conditions.append(NotificationLog.projcode.in_(list(projcodes)))
    if search:
        term = f'%{search.strip()}%'
        conditions.append(or_(
            NotificationLog.recipient.ilike(term),
            NotificationLog.projcode.ilike(term),
        ))
    return conditions


#: Binds this table to the shared helpers in ``querykit``. Declared last
#: because it closes over :func:`_filters`.
#:
#: ``dimensions`` insertion order is the order the vocabulary is quoted back in
#: the ``ValueError`` for an unknown facet — declare it the way the chips read.
SPEC = LogSpec(
    model=NotificationLog,
    id_column=NotificationLog.notification_log_id,
    order_columns=(NotificationLog.creation_time.desc(),),
    dimensions={
        'status': NotificationLog.status,
        'kind': NotificationLog.kind,
        'channel': NotificationLog.channel,
    },
    owned_filter={'status': 'statuses', 'kind': 'kinds',
                  'channel': 'channels'},
    build_filters=_filters,
)
