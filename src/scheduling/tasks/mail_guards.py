"""The two ways a task whose purpose is sending mail refuses to run.

Both are shared by every notice task rather than copied into each one,
because both are safety properties and a copy is a place for one of them to
quietly diverge. They live here rather than in `scheduling/registry.py`: the
runner knows nothing about mail, and a task that never sends any should not
import these.

Neither is expressible as a return value. :class:`~scheduling.registry.TaskResult`
has no failed state — it has `succeeded` and `partial` — so the only way to
make the run red is to raise.

⚠️ Do not reach for ``partial_failures`` instead. It means "some sent", and
in both cases below the count is exactly zero; an operator reading `partial`
would go looking for the ones that got through.
"""

from __future__ import annotations


class EmailCapExceeded(RuntimeError):
    """The audience exceeded the task's send cap. Nothing was sent.

    ``task_detail`` is merged into the ledger row by ``runner._execute``, so
    the audience and the cap land as structured data rather than as a
    substring of ``repr(exc)``.
    """

    def __init__(self, message: str, *, audience: int, cap: int) -> None:
        super().__init__(message)
        self.task_detail = {'audience': audience, 'cap': cap,
                            'aborted_before_sending': True}


class NotificationsDisabled(RuntimeError):
    """``NOTIFY_ENABLED`` is false in a context that exists to send mail.

    Without this a notice task would sail through: every message would be
    recorded `suppressed`, the run would report `succeeded`, the Job would go
    green, and nobody would learn that a chart change had stopped the mail.

    The CronJob does **not** inherit `webapp.env` — `cronjob-tasks.yaml`
    renders `.Values.tasks.env` plus a hand-listed set and nothing else — so
    this is a live failure mode and not a hypothetical one.
    """
