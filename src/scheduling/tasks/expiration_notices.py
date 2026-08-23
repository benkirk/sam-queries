"""``expiration_notices`` — email upcoming allocation expirations, weekly.

Monday 09:00 America/Denver. The first real consumer of both `sam/notify/`
and this package, and the first task at all that declares ``needs=('sam',)``.

⚠️ **A task computes from ``ctx.occurrence``, never from the wall clock.**
Here that is doubly load-bearing, because `ctx.occurrence` is naive **UTC**
while ``Allocation.end_date`` is naive **Mountain**: comparing them raw is a
6-7 hour skew, and a run dispatched 20 hours late would select a different
cohort than a punctual one. Both are fixed by converting to the schedule's
zone and truncating to local midnight — see :func:`window_start`.

**Why weekly, not monthly.** Runs 7 days apart with a 40-day band mean each
expiration is selected on 5-6 consecutive runs, so a skipped or failed week
is recovered by the next one and dedup prevents the double-send. A monthly
cadence gets one shot per expiration.

**Volume is spiky, not smooth.** 97% of allocations end on a month's last
day, and month-ends are ~30 days apart, so a weekly run's newly-entering
cohort — the 7-day band ``[run+33, run+40)`` — catches at most one cluster.
Measured against the snapshot: ~12 loaded runs a year peaking at ~535
messages, and ~40 runs sending 0-15. That shape is why the pre-filter in
:func:`_drop_already_notified` is permanent rather than an optimization.

**Kill recovery.** Killed mid-send (an `activeDeadlineSeconds` timeout, say)
leaves the ledger row `running`; the next hourly dispatch reclaims the stale
lease and re-runs; the re-run's `already_sent_many` suppresses everyone
already `sent`, so only the remainder goes. Nothing is sent twice and nothing
is lost — which is only true because the lease outlives the pod deadline. See
`expected_runtime` below.

Design: ``docs/plans/EXPIRATION_NOTICES.md``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import Weekly, to_local_naive
from scheduling.tasks._notice_common import (
    drop_already_notified,
    new_sam_session as _new_sam_session,
    positive_int_env,
    raise_if_disabled,
)
# Re-exported: these were defined here until a second notice task needed them,
# and callers (including this module's tests) still import them from here.
from scheduling.tasks.mail_guards import (   # noqa: F401
    EmailCapExceeded,
    NotificationsDisabled,
)

#: Monday 09:00 Mountain. Monday because a 500-recipient notice sent on a
#: Friday is read on Monday anyway, with any replies landing while the sender
#: is away — and because "first weekday of the month" lands on a Monday only
#: 42% of months and on a Friday 14%.
SCHEDULE = Weekly(0, 9, 0, tz='America/Denver')

#: The facilities this notice covers. **Explicit, never inherited** from the
#: Click default on `--facilities` (`cli/cmds/admin.py`): a CLI default is a
#: presentation choice someone may reasonably change, and the task must not
#: silently change audience when they do.
FACILITIES: Tuple[str, ...] = ('UNIV', 'WNA')

#: Reconnect the transport this often mid-send. See `Notifier.send_many`.
SEND_CHUNK = 250

#: Runaway guard, overridable via ``$SAM_TASKS_EMAIL_MAX``.
#:
#: ~4.7x the measured peak (535): far enough above normal operation never to
#: fire, close enough to catch an order-of-magnitude selection bug — a
#: milestone band that accidentally spans the whole table, say. § 12 proposed
#: 250, which is *below* observed volume and would fail every loaded run.
DEFAULT_EMAIL_MAX = 2500

#: How many failed recipients go into the ledger row. `detail` is TEXT and the
#: runner truncates at 60 kB; the summary email carries the full list.
_MAX_REPORTED_FAILURES = 50


def email_max(env: Optional[dict] = None) -> int:
    """The send cap, from ``$SAM_TASKS_EMAIL_MAX`` or the default.

    Read per run rather than at import, so a `values.yaml` change takes effect
    on the next dispatch rather than the next pod restart — the
    ``cleanup_status.retention_days`` pattern. Zero or negative is refused
    rather than obeyed (see :func:`positive_int_env`): it would abort every run,
    including the ones that should send nothing — indistinguishable from a
    broken query.
    """
    return positive_int_env('SAM_TASKS_EMAIL_MAX', DEFAULT_EMAIL_MAX, env)


def window_start(occurrence: datetime, *, tz: Optional[str] = None) -> datetime:
    """The local midnight the run's bands are measured from.

    Two conversions, each fixing a distinct bug:

    1. **UTC to local.** `ctx.occurrence` is naive UTC; `Allocation.end_date`
       is naive Mountain. Comparing them raw shifts every band by 6-7 hours.
    2. **Truncate to midnight.** Without it, a punctual 09:00 dispatch and one
       reclaimed at 05:00 the next morning compute different bands and select
       different cohorts for the same slot — which would make a re-run after a
       crash send to people the first attempt had already decided against.
    """
    local = to_local_naive(occurrence, ZoneInfo(tz or SCHEDULE.tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def band_bounds(start: datetime, milestone) -> Tuple[datetime, datetime]:
    """``[start + lo_days, start + hi_days)`` as query-ready bounds.

    ⚠️ **The half-open upper bound is built here, not assumed.**
    ``get_all_expiring_allocations`` filters ``end_date <= end_date`` —
    *inclusive* — so passing ``start + hi_days`` directly would make adjacent
    bands overlap on their shared boundary. With today's single rung that is
    invisible; with a three-rung ladder it double-sends to whoever lands on
    the seam. Subtracting a microsecond is what keeps the tiling honest.
    """
    lower = start + timedelta(days=milestone.lo_days)
    upper = (start + timedelta(days=milestone.hi_days)
             - timedelta(microseconds=1))
    return lower, upper


@task(name='expiration_notices',
      schedule=SCHEDULE,
      needs=('sam', 'status'),
      # Drives the LEASE, not a timeout — and the lease
      # (max(3x, 900s) = 3600s) must exceed the CronJob's
      # activeDeadlineSeconds (3000s), or a still-running send becomes
      # reclaimable and every PI gets a second copy. There is a drift test
      # asserting that inequality against helm/values.yaml, because the two
      # numbers live in different repositories of truth and nothing else
      # connects them. TaskContext exposes no ledger handle, so this task
      # cannot heartbeat; `Task.long_running` is the honest fix and is unbuilt.
      expected_runtime=timedelta(minutes=20),
      # 24h, not the 6h default. A late run is byte-identical because the
      # window comes from ctx.occurrence, so there is no reason to refuse one
      # — and 24h absorbs an ordinary maintenance window without writing a
      # `skipped` row that looks like a problem.
      misfire_grace=timedelta(hours=24),
      description='Email upcoming allocation-expiration notices')
def expiration_notices(ctx) -> TaskResult:
    """Notify PIs and project members whose allocations expire soon."""
    # Deferred: `scheduling/` is imported by the CLI's --list path, which must
    # not pay for jinja2 and the ORM to print a table.
    from sam.notify import Notifier
    from sam.notify.ledger import NotificationLedger
    from sam.queries.expiration_notices import (
        MILESTONES, build_expiration_messages,
    )
    from sam.queries.expirations import get_all_expiring_allocations

    start = window_start(ctx.occurrence)
    session = ctx.sam_session

    # 1. Select and build, one rung at a time. With today's single rung this
    #    is one query over [start, start+40); the loop is what makes adding
    #    rungs a one-tuple edit in sam.queries.expiration_notices.
    messages = []
    projcodes = set()
    for milestone in MILESTONES:
        lower, upper = band_bounds(start, milestone)
        selected = get_all_expiring_allocations(
            session,
            start_date=lower,
            end_date=upper,
            facility_names=list(FACILITIES),
            # The notice says "expires in N days"; measured from the slot, so
            # a late dispatch renders the same number a punctual one would.
            now=start,
        )
        ctx.logger.info('rung %s: %s to %s selected %d allocation(s)',
                        milestone.label, lower.date(), upper.date(),
                        len(selected))
        projcodes.update(p.projcode for p, _a, _r, _d in selected)
        messages.extend(build_expiration_messages(
            selected,
            # NOT getpass.getuser(): in this pod that is the runtime UID or a
            # KeyError, either way a lie in the column the admin card renders
            # as "who asked".
            requested_by='task:expiration_notices',
            milestone=milestone,
        ))

    notifier = Notifier(ledger=NotificationLedger(
        # The ledger's OWN sessions, off the engine rather than
        # `ctx.sam_session`: mail handed to a relay cannot be un-sent by the
        # rollback `close_sessions` performs when a task fails.
        lambda: _new_sam_session(session)))

    selected_count = len(messages)
    messages, suppressed_count = _drop_already_notified(notifier.ledger,
                                                        messages, ctx.logger)

    detail = {
        'window_start': start.isoformat(),
        'window_end': (start + timedelta(
            days=max(m.hi_days for m in MILESTONES))).isoformat(),
        'milestones': [m.label for m in MILESTONES],
        'projects': len(projcodes),
        # Always present, all three. A run that selected 0 must be visibly
        # different from one that selected 300 and suppressed them all —
        # otherwise ~40 legitimately-quiet weeks a year are indistinguishable
        # from a query that silently stopped matching.
        'selected': selected_count,
        'suppressed': suppressed_count,
        'audience': len(messages),
    }

    # 2. Guards, before any transport is touched.
    # No summary on the disabled path either: there is no working mailer to
    # send it with, and `preview`-ing one into the log would only look like it
    # went.
    raise_if_disabled(notifier)

    cap = email_max()
    if len(messages) > cap:
        reason = (f'audience of {len(messages)} exceeds '
                  f'SAM_TASKS_EMAIL_MAX={cap}; nothing was sent')
        # ⚠️ The summary goes out BEFORE the raise. Otherwise the one run Ben
        # most needs to hear about is the only one that emails him nothing —
        # he would learn of it as a red Job with no explanation attached.
        _send_summary(notifier, ctx, detail=detail, messages=messages,
                      failures=[], aborted=True, abort_reason=reason)
        raise EmailCapExceeded(reason, audience=len(messages), cap=cap)

    # 3. Send.
    if ctx.dry_run:
        for message in messages:
            notifier.preview(message)       # writes NO ledger row
        ctx.logger.info('dry run: %d message(s) rendered, none sent',
                        len(messages))
        # No summary: a dry run must write no ledger rows at all, and a
        # summary is a real message with a real row.
        return TaskResult(detail={**detail, 'sent': 0, 'failed': 0,
                                  'dry_run': True},
                          message=f'{len(messages)} previewed, none sent')

    if messages:
        ctx.logger.info('sending %d message(s) to %d project(s)',
                        len(messages), len(projcodes))
        results = notifier.send_many(messages, chunk_size=SEND_CHUNK)
    else:
        ctx.logger.info('nothing to notify (selected %d, suppressed %d)',
                        selected_count, suppressed_count)
        results = []

    failed = [r for r in results if not r.ok]
    sent = [r for r in results if r.status in ('sent', 'redirected')]
    detail.update({
        'sent': len(sent),
        'failed': len(failed),
        'failed_recipients': [r.recipient for r in failed[:_MAX_REPORTED_FAILURES]],
    })

    # Sent on the quiet weeks too. A summary that only arrived when there was
    # mail would make "no summary" mean both "nothing was due" and "the task
    # never ran", which is exactly the ambiguity it exists to remove.
    _send_summary(notifier, ctx, detail=detail, messages=messages,
                  failures=failed)

    return TaskResult(
        detail=detail,
        message=f'{len(sent)} sent, {len(failed)} failed, '
                f'{suppressed_count} already notified',
        # `partial` -> exit 2 -> a red Job. Deliberate: a hard bounce in a
        # 500-message run is worth seeing, and because volume is spiky the
        # quiet weeks stay reliably green, which makes a red one MORE
        # informative rather than less.
        partial_failures=len(failed))


def summary_recipient(env: Optional[dict] = None) -> str:
    """Where the per-run summary goes, from ``$SAM_TASKS_SUMMARY_TO``.

    Empty means "do not send one" — a legitimate configuration, and the
    default, so a developer running the task locally does not mail anyone.
    """
    return ((env or os.environ).get('SAM_TASKS_SUMMARY_TO') or '').strip()


def _send_summary(notifier, ctx, *, detail: dict, messages: List,
                  failures: List, aborted: bool = False,
                  abort_reason: Optional[str] = None) -> None:
    """Mail one operator a report of this run. Never raises.

    ⚠️ **A failure here must not fail the run.** By the time this is called
    the real mail has already gone out, and turning "the summary bounced"
    into a `failed` task would misreport several hundred successful
    deliveries — and, on the next dispatch, invite a re-run of them.

    The dedup key is the *occurrence*, so a reclaimed or manually re-run slot
    does not send a second summary. `--force` still overrides, as everywhere.
    """
    from sam.notify import Message, Recipient

    recipient = summary_recipient()
    if not recipient:
        ctx.logger.info('SAM_TASKS_SUMMARY_TO is unset; no run summary sent')
        return

    per_project: dict = {}
    for message in messages:
        if message.projcode:
            per_project[message.projcode] = per_project.get(message.projcode, 0) + 1

    if aborted:
        headline = 'ABORTED — nothing sent'
    elif detail.get('failed'):
        headline = (f"{detail.get('sent', 0)} sent, "
                    f"{detail['failed']} FAILED")
    elif detail.get('sent'):
        headline = f"{detail['sent']} sent to {len(per_project)} project(s)"
    else:
        headline = f"nothing due ({detail.get('suppressed', 0)} already notified)"

    try:
        notifier.send(Message(
            kind='task_summary',
            recipient=Recipient(recipient, name='SAM operator', role='admin'),
            subject=f'[SAM] {ctx.task_name}: {headline}',
            context={
                'task_name': ctx.task_name,
                'occurrence': ctx.occurrence.isoformat(),
                'headline': headline,
                'aborted': aborted,
                'abort_reason': abort_reason,
                'per_project': [{'projcode': code, 'count': count}
                                for code, count in sorted(per_project.items())],
                'failures': [{'recipient': r.recipient,
                              'detail': r.detail or '(no detail)'}
                             for r in failures],
                **detail,
            },
            # Keyed on the occurrence, not the clock: a reclaimed run filling
            # the same slot reports once.
            dedup_key=f'task_summary:{ctx.task_name}:{ctx.occurrence_key}'
                      f':{recipient}',
            requested_by=f'task:{ctx.task_name}',
        ))
    except Exception:
        ctx.logger.exception(
            'run summary could not be sent; the run itself is unaffected')


def _drop_already_notified(ledger, messages: List, logger) -> Tuple[List, int]:
    """Remove messages a previous run already delivered. Returns (kept, dropped).

    ⚠️ **This is permanent, and NOT redundant with ``Notifier``'s own dedup.**
    The framework would also suppress these — but it would suppress them by
    *recording a ``suppressed`` row for each one*. On a loaded week ~85% of
    the selection is already-notified and on a quiet week essentially all of
    it is, so leaving it to the framework writes on the order of **26,000
    rows a year** into `notification_log` — the same table the admin
    Notifications card, its facet chips, and the last-notified badge all read.

    Dropping them here means a quiet week writes zero rows and reports
    ``audience: 0``. Nothing is lost: the count is still in
    ``TaskResult.detail``.

    On a loaded week ~85% of the selection is already-notified and on a quiet
    week essentially all of it is, so leaving this to the framework would write
    on the order of **26,000 rows a year** into `notification_log` — the same
    table the admin Notifications card, its facet chips, and the last-notified
    badge all read.

    The **legacy** half of the key list is the only extra part here. Every
    manual CLI run before the rung label existed wrote
    ``expiration:{projcode}:{date}:{recipient}``; without checking that form
    too, the first scheduled run re-notifies the overlap cohort. After one
    full cycle every live key is in the new format and
    :func:`~sam.queries.expiration_notices.legacy_dedup_key` can go — at which
    point this wrapper drops the ``legacy_key`` and becomes the bare shared
    call.
    """
    from sam.queries.expiration_notices import legacy_dedup_key

    def legacy_key(message):
        # `expiration:{projcode}:{date}:{label}:{recipient}` -> drop the label.
        parts = (message.dedup_key or '').split(':')
        if len(parts) == 5:
            return legacy_dedup_key(parts[1], parts[2], parts[4])
        return None

    return drop_already_notified(ledger, messages, logger,
                                 legacy_key=legacy_key)


# `_new_sam_session` is `new_sam_session` from `_notice_common` (imported above
# under that name) — the fresh ledger session shared with `xras_notices`.
