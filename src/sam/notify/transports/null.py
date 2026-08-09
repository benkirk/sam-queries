"""``NullTransport`` — records, never sends. The test-tier default.

``TestingConfig`` pins ``NOTIFY_TRANSPORT='null'`` on the same reasoning it
already zeroes cache TTLs (``webapp/config.py:337``): a test tier that *can*
reach shared state is a test tier that eventually does. § 9 measured that any
host on the VPN reaches ``ndir`` and is accepted for arbitrary recipients, so
"shared state" here means the internet.

It keeps every message it was handed, which is what lets a test assert on what
*would* have gone out without asserting on ``smtplib`` internals.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from sam.notify.base import Channel, Message, RenderedMessage, Transport

logger = logging.getLogger(__name__)


class NullTransport(Transport):
    """Accepts everything, delivers nothing, remembers it all."""

    name = 'null'
    channel = Channel.EMAIL

    def __init__(self, config=None) -> None:
        self.config = config
        #: Every ``(message, rendered)`` handed to :meth:`deliver`, in order.
        self.delivered: List[Tuple[Message, RenderedMessage]] = []
        #: How many batches were opened — the assertion behind "one
        #: connection per batch, not per message".
        self.open_count = 0
        self.close_count = 0

    def open(self) -> None:
        self.open_count += 1

    def close(self) -> None:
        self.close_count += 1

    def deliver(self, message: Message, rendered: RenderedMessage) -> None:
        self.delivered.append((message, rendered))
        logger.debug('notify[null]: kind=%s to=%s subject=%r',
                     message.kind, message.recipient.address, rendered.subject)
