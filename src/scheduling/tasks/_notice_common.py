"""Shared plumbing for the notice tasks (`expiration_notices`, `xras_notices`).

Both tasks mail from `sam/notify/` on a schedule, and both had grown their own
copies of four small pieces: a fresh ledger session, the permanent
already-notified pre-drop, a positive-int env reader, and the
`NOTIFY_ENABLED`-is-false guard. Each copy is a place for a **safety** property
to drift — the pre-drop and the guard especially — so they live here once, the
same way :mod:`scheduling.tasks.mail_guards` already homes the two raise types.

Kept deliberately thin and import-light: `os`, a lazy SQLAlchemy import, and
`mail_guards`. Nothing here imports Click / Flask / rich / kubernetes (the
`scheduling` package ban, AST-checked by ``test_task_ledger.py``), and nothing
here registers a task.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

from scheduling.tasks.mail_guards import NotificationsDisabled


def new_sam_session(existing):
    """A fresh SAM session on the same engine as the task's own.

    The ledger must commit independently of the task's transaction, so it
    cannot share `ctx.sam_session`. Deriving the engine from that session
    rather than calling `create_sam_engine()` again keeps one pool.
    """
    from sqlalchemy.orm import Session
    return Session(existing.get_bind())


def positive_int_env(name: str, default: int,
                     env: Optional[dict] = None) -> int:
    """Read ``$name`` as a positive int, else ``default``.

    Read per run rather than at import, so a `values.yaml` change takes effect
    on the next dispatch rather than the next pod restart — the
    `cleanup_status.retention_days` pattern.

    A missing, blank, non-numeric, zero **or negative** value all fall back to
    the default. Zero or negative is refused rather than obeyed: for a send cap
    it would abort every run including the ones that should send nothing, which
    is indistinguishable from a broken query.
    """
    raw = (env or os.environ).get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def drop_already_notified(
        ledger, messages: List, logger, *,
        legacy_key: Optional[Callable[[object], Optional[str]]] = None
        ) -> Tuple[List, int]:
    """Remove messages a previous run or an operator already delivered.

    WARNING: **Permanent, and NOT redundant with ``Notifier``'s own dedup.** The
    framework would also suppress these — by *recording a ``suppressed`` row for
    each one* into `notification_log`, the same table the admin Notifications
    card, its facet chips and the last-notified badge all read. Dropping them
    here means a quiet run writes zero rows and reports ``audience: 0``; nothing
    is lost, the count is in ``TaskResult.detail``. Each caller's docstring
    carries its own measured volume for why this matters.

    ``legacy_key(message) -> Optional[str]`` lets a caller also check an older
    dedup-key form for the same message (expiration's pre-label keys); when a
    legacy key is suppressed, its message is dropped too. Callers with a single
    key form pass nothing.
    """
    if not messages or ledger is None:
        # `Notifier(ledger=None)` is a documented configuration ("record
        # nothing"). It cannot answer the suppression question, so nothing is
        # dropped — matching `_pre_transport_guard`.
        return messages, 0

    legacy = {}
    if legacy_key is not None:
        for message in messages:
            key = legacy_key(message)
            if key is not None:
                legacy[message.dedup_key] = key

    keys = [m.dedup_key for m in messages if m.dedup_key]
    suppressed = ledger.already_sent_many(keys + list(legacy.values()))
    kept = [m for m in messages
            if not (m.dedup_key in suppressed
                    or legacy.get(m.dedup_key) in suppressed)]
    dropped = len(messages) - len(kept)
    if dropped:
        logger.info('%d of %d message(s) already notified; not re-recording',
                    dropped, len(messages))
    return kept, dropped


def raise_if_disabled(notifier) -> None:
    """Raise :class:`NotificationsDisabled` unless the mailer is actually on.

    The guard every notice task runs before touching transport. Without it a
    notice task sails through: every message records `suppressed`, the run
    reports `succeeded`, the Job goes green, and nobody learns the mail stopped.
    The CronJob does **not** inherit `webapp.env` (`cronjob-tasks.yaml` renders
    `.Values.tasks.env` plus a hand-listed set and nothing else), so this is a
    live failure mode, not a hypothetical one.
    """
    if not notifier.config.enabled:
        raise NotificationsDisabled(
            'NOTIFY_ENABLED is false; refusing to run a task whose only '
            'purpose is to send mail. Check the CronJob env — it does not '
            'inherit webapp.env.')
