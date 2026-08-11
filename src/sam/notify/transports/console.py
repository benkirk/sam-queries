"""``ConsoleTransport`` — renders to a sink instead of a relay.

The dev and ``--dry-run`` transport. Unlike ``null`` it shows the operator the
actual body, which is the difference between "the pipeline ran" and "the mail
says what I meant".

Deliberately writes through a plain file object rather than ``rich``: ``sam/``
stays free of the CLI's presentation stack, and the CLI keeps its progress bar
through :class:`~sam.notify.service.Notifier`'s ``on_result`` callback.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

from sam.notify.base import Channel, Message, RenderedMessage, Transport

logger = logging.getLogger(__name__)

_RULE = '─' * 72


class ConsoleTransport(Transport):
    """Writes each message to ``stream`` (default ``sys.stdout``)."""

    name = 'console'
    channel = Channel.EMAIL

    def __init__(self, config=None, stream: TextIO | None = None) -> None:
        self.config = config
        self._stream = stream
        self.delivered: list[tuple[Message, RenderedMessage]] = []

    @property
    def stream(self) -> TextIO:
        # Resolved late so a test that captures stdout after construction
        # still sees the redirect.
        return self._stream if self._stream is not None else sys.stdout

    def deliver(self, message: Message, rendered: RenderedMessage) -> None:
        self.delivered.append((message, rendered))
        out = self.stream
        from_addr = getattr(self.config, 'mail_from', 'sam-admin@ucar.edu')
        out.write(f'{_RULE}\n')
        out.write(f'kind:      {message.kind}\n')
        out.write(f'from:      {from_addr}\n')
        out.write(f'to:        {message.recipient.address}\n')
        if message.intended_recipient:
            out.write(f'intended:  {message.intended_recipient}\n')
        out.write(f'subject:   {rendered.subject}\n')
        out.write(f'template:  {rendered.template_text}\n')
        out.write(f'{_RULE}\n')
        out.write(rendered.text)
        if not rendered.text.endswith('\n'):
            out.write('\n')
        out.write('\n')
        out.flush()
