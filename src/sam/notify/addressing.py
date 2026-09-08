"""``AddressingStore`` — operator-added cc/bcc rows, read once per Notifier.

Same discipline as ``OverrideLoader`` in :mod:`sam.notify.render`: one
``SELECT`` on first use through the ledger's session factory, no factory
means no query, and a database failure (the table not created yet, an
outage) logs one warning and adds nothing. Mail is never withheld for it.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select

from sam.notify.addressing_store import NotificationAddressing
from sam.notify.config import Addressing
from sam.notify.kinds import message_scopes

logger = logging.getLogger(__name__)

Copies = Tuple[Tuple[str, ...], Tuple[str, ...]]


class AddressingStore:
    """The ``notification_addressing`` rows, as ``(scope, field, address)``."""

    def __init__(self, session_factory=None) -> None:
        self.session_factory = session_factory
        self._rows: Optional[List[Tuple[str, str, str]]] = None

    @property
    def rows(self) -> List[Tuple[str, str, str]]:
        if self._rows is None:
            self._rows = self._load()
        return self._rows

    def _load(self) -> List[Tuple[str, str, str]]:
        if self.session_factory is None:
            return []
        try:
            with self.session_factory() as session:
                rows = session.execute(select(NotificationAddressing).order_by(
                    NotificationAddressing.notification_addressing_id)).scalars()
                return [(r.scope, r.field, r.address) for r in rows]
        except Exception as exc:
            # Any failure, not only SQLAlchemyError: this read only adds
            # copies, and the ledger decides fail-closed for a dead database.
            logger.warning('notify: operator addressing unavailable, using the '
                           'deployment defaults only: %s', exc)
            return []

    def extra(self, scopes: Sequence[str]) -> Copies:
        """``(cc, bcc)`` added for a message matching ``scopes``, in scope order."""
        cc, bcc = [], []
        for scope in scopes:
            for row_scope, field, address in self.rows:
                if row_scope == scope:
                    (cc if field == 'cc' else bcc).append(address)
        return tuple(cc), tuple(bcc)

    def effective(self, env: Addressing, kind: str, facility: Optional[str] = None,
                  *, cc: Iterable[str] = (), bcc: Iterable[str] = (),
                  sender: Optional[str] = None,
                  reply_to: Optional[str] = None) -> Addressing:
        """Merge: a builder-set ``cc``/``bcc`` replaces the env default, the
        operator rows always add; ``sender``/``reply_to`` are env-only."""
        extra_cc, extra_bcc = self.extra(message_scopes(kind, facility))
        return Addressing(
            cc=_dedupe(tuple(cc) or env.cc, extra_cc),
            bcc=_dedupe(tuple(bcc) or env.bcc, extra_bcc),
            sender=sender or env.sender,
            reply_to=reply_to or env.reply_to,
        )


def _dedupe(*groups: Iterable[str]) -> Tuple[str, ...]:
    seen, out = set(), []
    for group in groups:
        for address in group:
            key = address.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(address)
    return tuple(out)
