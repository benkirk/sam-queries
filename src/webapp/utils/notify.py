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

from flask import current_app, request, url_for
from sqlalchemy.orm import Session

from sam.notify import Notifier, NotifyConfig
from sam.notify.ledger import NotificationLedger
from webapp.extensions import db

logger = logging.getLogger(__name__)


def public_url_root() -> str:
    """Root for links in outgoing mail: PUBLIC_BASE_URL, else this request's
    host. An operator on the cluster hostname must not mail that name to PIs."""
    base = current_app.config.get('PUBLIC_BASE_URL') or request.url_root
    return base.rstrip('/') + '/'


def public_url_for(endpoint: str, **values) -> str:
    """``url_for`` rooted at :func:`public_url_root`."""
    return public_url_root() + url_for(endpoint, **values).lstrip('/')


def get_notifier(*, read_only: bool = False) -> Notifier:
    """Build a :class:`~sam.notify.Notifier` for this request.

    Config is read per call from ``app.config``, never memoised: a cached
    notifier would outlive a test's config override and hold a dead socket.
    The ledger is always session-backed, because its factory is also what loads
    operator template overrides and addressing rows; ``read_only`` is for
    previews, whose notifier cannot record and therefore cannot send.
    """
    return Notifier(ledger=NotificationLedger(lambda: Session(db.engine),
                                              read_only=read_only))


def notify_config() -> NotifyConfig:
    """The notify config alone, for callers that only show enabled/redirect."""
    return NotifyConfig.from_environment()


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
