"""The webapp's entry point into :mod:`sam.notify`.

One function, and the reason it exists is the session it hands the ledger.

Route handlers run inside ``management_transaction(db.session)``, which rolls
the whole session back on exception — correct for a *decision*, wrong for a
*delivery*. Mail handed to a relay cannot be un-sent by a rollback, so the
ledger must not enrol in the request's transaction. It therefore gets a
factory that opens fresh sessions on ``db.engine``, the same discipline
``webapp/api/xras/recheck.py`` uses for its audit row.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 5 and § 7.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from sam.notify import Notifier
from sam.notify.ledger import NotificationLedger
from webapp.extensions import db

logger = logging.getLogger(__name__)


def get_notifier(*, ledger: bool = True) -> Notifier:
    """Build a :class:`~sam.notify.Notifier` for this request.

    Config comes from ``app.config`` via ``NotifyConfig`` (the Flask half of
    the framework-agnostic seam), so ``NOTIFY_ENABLED`` and friends are read
    per call rather than memoised at import — a `Notifier` cached at module
    scope would outlive a config override in a test and, worse, would hold a
    transport whose socket had long since closed.

    Args:
        ledger: set False only for a pure ``preview()``, which writes no row
            and so needs no database at all.
    """
    return Notifier(
        ledger=NotificationLedger(lambda: Session(db.engine)) if ledger else None
    )


def notify_summary(results) -> dict:
    """Fold ``list[DeliveryResult]`` into counts a template can render.

    ``redirected`` is kept separate from ``sent`` all the way to the UI: it
    reached ``NOTIFY_REDIRECT_TO``, not the person it names, and a staging box
    quietly reporting "sent" is the failure mode that whole mode exists to
    prevent.
    """
    summary = {'sent': [], 'redirected': [], 'suppressed': [], 'failed': []}
    for result in results:
        summary.setdefault(result.status, []).append(result)
    summary['delivered'] = summary['sent'] + summary['redirected']
    summary['ok'] = bool(summary['delivered'])
    return summary
