"""``xras_notices`` — mail the XRAS handoff notices nobody clicked.

Hourly, 08:00-17:00 Mon-Fri Mountain. The second consumer of `sam/notify/`
on a schedule, and the second task declaring ``needs=('sam',)``.

Every processed XRAS action that changes an allocation already has a notice
written for it and a **Notify** button on the Allocations -> XRAS card. If
nobody presses it, the PI is never told: the extension happened, the
supplement landed, and the only missing step is that anyone said so. This is
that button, on a schedule, for the four services where the outcome speaks
for itself.

WARNING: **A task computes from ``ctx.occurrence``, never from the wall clock.**
Here ``ctx.occurrence`` is naive **UTC** and ``XrasActionLog.received_time``
is naive **Mountain** — stamped from the app clock precisely because MySQL's
`CURRENT_TIMESTAMP` resolves in UTC in the containers (see
``sam/integration/xras.py``). Comparing them raw is a 6-7 hour skew in the
one predicate that decides whether a PI gets mailed today or tomorrow.

**Why a delay at all.** An operator who is about to activate, correct or
dismiss an action needs a window in which the machine has not already spoken
for them. One day is long enough for that and short enough that nobody is
waiting on us.

**Why business hours.** A notice sent at 03:00 is read at 09:00 anyway, and
any reply lands while nobody is watching. `BusinessHourly` removes the
overnight case structurally rather than by a quiet-hours check here.

WARNING: **The effective delay is longer than the nominal one**, and `detail`
reports the real age of what went out so this never reads as a stuck queue::

    received Tue 09:15, after=1d  ->  eligible Wed 09:15  ->  sends Wed 10:00
    received Fri 16:30, after=1d  ->  eligible Sat 16:30  ->  sends Mon 08:00

**Why it cannot double-mail.** The manual button and this task mint
*identical* dedup keys (``{kind}:{projcode}:{action_id}:{address}``) because
both call :func:`sam.queries.xras_notices.build_xras_messages`. Whichever
fires second is suppressed by the ledger. That is the whole safety argument,
and the reason no locking or claiming is needed around the card.

**Kill recovery.** Killed mid-send leaves the ledger row `running`; the next
dispatch reclaims the stale lease and re-runs; `already_sent_many` suppresses
everyone already `sent`. Nothing is sent twice and nothing is lost — and
because the selection window is *rolling* rather than banded, even a wholly
forfeited slot is covered by the next one an hour later.

Design: ``docs/plans/XRAS_AUTO_NOTICES.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import BusinessHourly, to_local_naive
from scheduling.tasks._notice_common import (
    drop_already_notified,
    new_sam_session as _new_sam_session,
    positive_int_env,
    raise_if_disabled,
)
from scheduling.tasks.mail_guards import EmailCapExceeded

#: Ten slots a day, on the hour. `minute=0` rather than an offset because the
#: CronJob wakes at :07 — a :00 slot is dispatched about seven minutes later,
#: where a :20 slot would wait until the *next* wake and add ~47 minutes to
#: every notice.
SCHEDULE = BusinessHourly(minute=0, tz='America/Denver')


@dataclass(frozen=True)
class AutoNotice:
    """One service that may be notified without an operator, and how long to
    wait first."""

    service: str
    after: timedelta


#: **Fail-closed: a service absent here is NEVER auto-sent.**
#:
#: * ``add`` is absent on purpose. A New is *two* writes, not one — the notice
#:   says "is now active", and the project is not, until somebody activates it.
#:   The pre-deploy smoke measured a manual notice going out 64 seconds before
#:   the activation it announced. An unattended sender has no operator to
#:   correct that order, so automatic activation is a separate decision (with
#:   a governance question attached) and stays unbuilt.
#: * ``transfer`` has no notification kind at all — it parks as `manual` by
#:   design and never completes, so it can never be selected even by mistake.
#:
#: A ``timedelta`` rather than a `days=`/`hours=` pair because that is already
#: the idiom in this package (`misfire_grace`, `expected_runtime`): a policy row
#: reads like the decorator below it, hours cost nothing, there is no unit
#: ambiguity in the ledger `detail`, and there is no both-set/neither-set
#: validator to write.
AUTO_NOTICES: Tuple[AutoNotice, ...] = (
    AutoNotice('update',     after=timedelta(days=1)),
    AutoNotice('extend',     after=timedelta(days=1)),
    AutoNotice('supplement', after=timedelta(days=1)),
    AutoNotice('adjust',     after=timedelta(days=1)),
)

#: How far back the cohort may reach. An action older than this is never
#: auto-sent; it stays manual.
#:
#: This is a blast-radius bound, not a feature. Without it the first run after
#: the kill switch clears would mail every never-notified action in the table
#: at once. Two weeks leaves ~13 days of self-healing after the one-day delay —
#: an outage long enough to exhaust that is long enough to want a human.
LOOKBACK = timedelta(days=14)

#: Runaway guard, overridable via ``$SAM_TASKS_XRAS_MAX``.
#:
#: Sized to *this* task's traffic, not shared with `expiration_notices`' 2500.
#: A normal hour sends single digits, so 50 is far enough above normal
#: operation never to fire and close enough that an inverted predicate — a
#: dropped `not` on the already-notified check, say — trips it on the first
#: run instead of mailing a fortnight of history.
DEFAULT_XRAS_MAX = 50

#: Cap on rows echoed into the ledger row. `detail` is TEXT and the runner
#: truncates the JSON at 60 kB.
_MAX_REPORTED = 100


def notify_after(env: Optional[dict] = None) -> Optional[timedelta]:
    """Global override for every policy row's ``after``, or None.

    From ``$SAM_XRAS_NOTIFY_AFTER_HOURS``. **Hours**, because it is the finer
    unit — "24" is a day and "6" is six hours — and because a single env var
    is the whole point: tuning the delay during an incident must not need a
    code deploy.

    Read per run rather than at import, so a `values.yaml` change lands on the
    next dispatch rather than the next pod restart — the
    `cleanup_status.retention_days` pattern.

    Zero or negative is refused rather than obeyed: it would mean "mail the
    instant an action lands", which removes the entire point of the delay, and
    is far more likely to be a typo than an intent.
    """
    raw = (env or os.environ).get('SAM_XRAS_NOTIFY_AFTER_HOURS')
    if raw is None or not str(raw).strip():
        return None
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return None
    return timedelta(hours=hours) if hours > 0 else None


def xras_email_max(env: Optional[dict] = None) -> int:
    """The send cap, from ``$SAM_TASKS_XRAS_MAX`` or :data:`DEFAULT_XRAS_MAX`.

    Same shape and same reasoning as `cleanup_status.retention_days` and
    `expiration_notices.email_max`: read per run, and a zero or negative value
    is refused rather than obeyed — it would abort every run including the ones
    that should send nothing, which is indistinguishable from a broken query.
    """
    return positive_int_env('SAM_TASKS_XRAS_MAX', DEFAULT_XRAS_MAX, env)


def policy(env: Optional[dict] = None) -> Dict[str, timedelta]:
    """``{service: after}`` for this run, with the env override applied."""
    override = notify_after(env)
    return {rule.service: (override if override is not None else rule.after)
            for rule in AUTO_NOTICES}


def select(rows, *, slot: datetime, delays: Dict[str, timedelta]) -> List[dict]:
    """The rows this slot may notify, from ``get_xras_activity`` output.

    WARNING: **Keyed on ``service``, never on ``action_type``.** The card's badge
    shows `action_type`, and the two disagree: `sam.xras.dispatch` routes a
    **New** whose projcode already exists to the ``update`` service, and
    ``update`` is in the auto set — so a row badged `New` can auto-send. That
    is correct (it is a renewal in all but name and the project needs no
    activation) and it surprises everybody, so there is a test that names it.

    `action_type` could not be used even if it did agree: it is nullable
    ("NULL when the body could not be parsed"), it has aliases (`Adjust` /
    `Adjustment`), it is deliberately unconstrained so an unrecognized value
    still lists, and it includes `'Date Adjustment'`, which no service handles.
    `service` is the constrained six-member vocabulary and is already what
    decides whether a row is notifiable at all.

    ``notified`` is true when **any** recipient of that action was reached, so
    a half-delivered action is treated as done and stays manual. That is the
    conservative direction — never double-mail — and it matches what the card
    shows the operator.
    """
    selected = []
    for row in rows:
        after = delays.get(row.get('service') or '')
        if after is None:
            continue
        if not row.get('notifiable') or row.get('notified') or row.get('dismissed'):
            continue
        received = row.get('received_time')
        if received is None or received > slot - after:
            continue
        selected.append(row)
    return selected


@task(name='xras_notices',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the LEASE, not a timeout, and the lease (max(3x, 900s) = 3600s)
      # must exceed the CronJob's activeDeadlineSeconds (3000s) — otherwise a
      # send killed by the pod deadline becomes reclaimable while it is still
      # sending, and the two runs can race past each other's ledger writes.
      # The same drift test `expiration_notices` carries asserts that
      # inequality against helm/values.yaml.
      #
      # A real run takes seconds; this number is not an estimate, it is the
      # floor that keeps a killed run un-reclaimable. `TaskContext` exposes no
      # ledger handle, so the task cannot heartbeat instead.
      expected_runtime=timedelta(minutes=20),
      # The 6h default, unlike the other two notice tasks. A misfire costs
      # nothing here: the selection window is rolling rather than banded, so
      # the next slot an hour later covers everything this one would have. A
      # longer grace would only run a stale slot that the fresh one already
      # subsumes.
      misfire_grace=timedelta(hours=6),
      description='Email XRAS outcome notices nobody sent by hand')
def xras_notices(ctx) -> TaskResult:
    """Notify project leads and admins about processed XRAS actions."""
    # Deferred: `scheduling/` is imported by the CLI's --list path, which must
    # not pay for jinja2 and the ORM to print a table.
    from sam.integration.xras import XrasActivationEvent
    from sam.notify import Notifier
    from sam.notify.ledger import NotificationLedger
    from sam.projects.projects import Project
    from sam.queries.xras_activation import (
        get_xras_activity, get_xras_pending_recipients,
    )
    from sam.queries.xras_notices import build_xras_messages, load_xras_action

    # ONE conversion, zone read off SCHEDULE rather than repeated as a literal.
    # No truncation to local midnight, unlike `expiration_notices.window_start`:
    # there the occurrence defines selection *bands* that must tile identically
    # across a re-run. Here it is a rolling threshold, and an hourly schedule
    # truncated to midnight would compare every slot against the same instant.
    slot = to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz))
    session = ctx.sam_session

    delays = policy()
    # `until=slot` as well as `since=`: a run reclaimed an hour late must
    # select the cohort ITS slot would have, not a wider one. Without it a late
    # dispatch and a punctual one disagree about the same occurrence key.
    rows = get_xras_activity(session, since=slot - LOOKBACK, until=slot)
    candidates = select(rows, slot=slot, delays=delays)

    ctx.logger.info('as of %s: %d action(s) in [%s, %s] -> %d eligible',
                    slot.isoformat(), len(rows),
                    (slot - LOOKBACK).date(), slot.date(), len(candidates))

    per_service: Dict[str, int] = {}
    for row in candidates:
        per_service[row['service']] = per_service.get(row['service'], 0) + 1

    # One recipient query for the whole cohort, not one per action.
    project_ids = sorted({row['project_id'] for row in candidates})
    recipients = get_xras_pending_recipients(session, project_ids)
    projects = {p.project_id: p for p in
                session.query(Project)
                .filter(Project.project_id.in_(project_ids)).all()} \
        if project_ids else {}

    messages = []
    by_key: Dict[str, dict] = {}        # dedup_key -> the row that minted it
    for row in candidates:
        project = projects.get(row['project_id'])
        people = recipients.get(row['project_id'], [])
        if project is None or not people:
            # No lead or admin email on file. Not an error — an operator may
            # have reached them out of band — but it must be visible, or a
            # project silently never gets told anything.
            ctx.logger.info('%s action %s: no recipients on file',
                            row['projcode'], row['action_log_id'])
            continue
        built = build_xras_messages(
            session, project, people,
            action=load_xras_action(session, row['action_log_id']),
            # NOT getpass.getuser(): in this pod that is the runtime UID or a
            # KeyError, either way a lie in the column the admin card renders
            # as "who asked".
            requested_by='task:xras_notices')
        for message in built:
            by_key[message.dedup_key] = row
        messages.extend(built)

    notifier = Notifier(ledger=NotificationLedger(
        # The ledger's OWN sessions, off the engine rather than
        # `ctx.sam_session`: mail handed to a relay cannot be un-sent by the
        # rollback `close_sessions` performs when a task fails.
        lambda: _new_sam_session(session)))

    selected_count = len(messages)
    messages, suppressed_count = _drop_already_notified(notifier.ledger,
                                                        messages, ctx.logger)

    detail = {
        'window_start': (slot - LOOKBACK).isoformat(),
        'window_end': slot.isoformat(),
        'delays_hours': {service: after.total_seconds() / 3600
                         for service, after in sorted(delays.items())},
        'actions': len(candidates),
        'by_service': dict(sorted(per_service.items())),
        # Always present, all three. A run that selected 0 must be visibly
        # different from one that selected 30 and suppressed them all — most
        # hours legitimately send nothing, and "0 sent, succeeded" has to stay
        # distinguishable from a query that quietly stopped matching.
        'selected': selected_count,
        'suppressed': suppressed_count,
        'audience': len(messages),
    }

    # Guards, before any transport is touched.
    raise_if_disabled(notifier)

    cap = xras_email_max()
    if len(messages) > cap:
        raise EmailCapExceeded(
            f'audience of {len(messages)} exceeds SAM_TASKS_XRAS_MAX={cap}; '
            f'nothing was sent',
            audience=len(messages), cap=cap)

    if ctx.dry_run:
        for message in messages:
            notifier.preview(message)       # writes NO ledger row
        ctx.logger.info('dry run: %d message(s) rendered, none sent',
                        len(messages))
        return TaskResult(detail={**detail, 'sent': 0, 'failed': 0,
                                  'dry_run': True},
                          message=f'{len(messages)} previewed, none sent')

    if messages:
        ctx.logger.info('sending %d message(s) for %d action(s)',
                        len(messages), len(candidates))
        results = notifier.send_many(messages)
    else:
        results = []

    failed = [r for r in results if not r.ok]
    sent = [r for r in results if r.status in ('sent', 'redirected')]

    delivered_rows = _record_notified(session, XrasActivationEvent, sent,
                                      by_key, ctx.logger)

    detail.update({
        'sent': len(sent),
        'failed': len(failed),
        'failed_recipients': [r.recipient for r in failed[:_MAX_REPORTED]],
        'actions_notified': sorted(delivered_rows),
        # The REAL age of what went out. The nominal delay is a floor: a Friday
        # afternoon arrival waits the weekend, so ~2.6 days is a correct result
        # and must not read as a backlog.
        'oldest_sent_age_hours': _oldest_age_hours(slot, sent, by_key),
    })

    return TaskResult(
        detail=detail,
        message=f'{len(sent)} sent, {len(failed)} failed, '
                f'{suppressed_count} already notified',
        partial_failures=len(failed))


def _drop_already_notified(ledger, messages: List, logger) -> Tuple[List, int]:
    """Remove messages a previous run or an operator already delivered.

    WARNING: **This is permanent, and NOT redundant with ``Notifier``'s own dedup.**
    The framework would also suppress these — by *recording a ``suppressed``
    row for each one*. This task wakes fifty times a week and most of those
    runs have nothing new, so leaving it to the framework would write a steady
    drip of rows into `notification_log`: the same table the admin
    Notifications card, its facet chips and the last-notified badge all read.

    Dropping them here means a quiet hour writes zero rows and reports
    ``audience: 0``. Nothing is lost — the count is in ``TaskResult.detail``.

    The row-level ``notified`` filter in :func:`select` catches most of these
    already; this is the per-**address** check, and it is what closes the race
    with an operator pressing Notify between the query and the send.

    This task wakes ~fifty times a week and most runs have nothing new, so the
    rows this pre-drop avoids are the dominant write into `notification_log`.
    Single key form, so no ``legacy_key`` — the shared core does the rest.
    """
    return drop_already_notified(ledger, messages, logger)


def _record_notified(session, XrasActivationEvent, sent, by_key,
                     logger) -> List[int]:
    """One ``notified`` event per action that actually reached somebody.

    **Send first, record second**, exactly as the manual route does: the
    event's ``notified_to`` names the addresses that *succeeded*, so the
    timeline never claims a handoff that did not leave the building. An action
    whose every message failed gets no event, because nothing happened.

    Not needed for the card's badge — ``row['notified']`` derives from
    `notification_log` through the parsed dedup key and works without this —
    but the operator history modal would otherwise show a notice appearing
    from nowhere, with no answer to "who sent this".

    Written on the task's own session; the runner commits it, and rolls it
    back under `--dry-run` (which never reaches here, because a dry run
    previews instead of sending).
    """
    by_action: Dict[int, List] = {}
    rows: Dict[int, dict] = {}
    for result in sent:
        row = by_key.get(result.message.dedup_key)
        if row is None:
            continue
        by_action.setdefault(row['action_log_id'], []).append(result)
        rows[row['action_log_id']] = row

    for action_id, results in sorted(by_action.items()):
        row = rows[action_id]
        notified_to = '; '.join(
            f"{r.message.recipient.name or r.message.recipient.address} "
            f"<{r.message.recipient.address}>" for r in results) or None
        XrasActivationEvent.create(
            session,
            project_id=row['project_id'],
            event_type='notified',
            # `users.username`-shaped and capped at 35 chars; this is 17.
            created_by='task:xras_notices',
            comment='Sent automatically by the xras_notices task.',
            notified_to=notified_to,
            # The action actually reported, never "whatever is newest by now".
            xras_action_log_id=action_id,
        )
        logger.info('%s action %s: recorded notified -> %s',
                    row['projcode'], action_id, notified_to)
    return sorted(by_action)


def _oldest_age_hours(slot: datetime, sent, by_key) -> Optional[float]:
    """Age, in hours at the slot, of the oldest action this run mailed."""
    ages = [(slot - by_key[r.message.dedup_key]['received_time']).total_seconds()
            for r in sent if by_key.get(r.message.dedup_key)]
    return round(max(ages) / 3600, 1) if ages else None


# `_new_sam_session` is `new_sam_session` from `_notice_common` (imported above
# under that name) — the fresh ledger session shared with `expiration_notices`.
