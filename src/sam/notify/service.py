"""``Notifier`` — the entire public surface of :mod:`sam.notify`.

Callers build :class:`~sam.notify.base.Message` objects and hand them here.
Everything else — config, template resolution, the safety guards, the
transport lifecycle and (from the ledger commit) the ``notification_log``
rows — happens behind this class.

**The guard order is the design.** Each guard is cheaper and more final than
the one after it:

1. an unknown ``kind`` **raises** — that is a programmer error, not an
   outcome, and it must not be recordable;
2. ``NOTIFY_ENABLED`` off → ``suppressed``, without rendering;
3. already sent under this ``dedup_key`` → ``suppressed``;
4. ``NOTIFY_REDIRECT_TO`` set → the address is rewritten and the outcome is
   ``redirected``, never ``sent``;
5. the transport runs → ``sent`` or ``failed``.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 1, § 3 and § 5.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Iterable, List, Optional

from sam.notify.base import (
    DeliveryResult, Message, Recipient, RenderedMessage, Transport,
    TransportError,
)
from sam.notify.config import NotifyConfig
from sam.notify.kinds import get_kind
from sam.notify.ledger import LedgerError
from sam.notify.registry import build_transport
from sam.notify.render import TemplateRenderer

logger = logging.getLogger(__name__)

#: Prepended to a redirected body so a staging inbox cannot be mistaken for
#: production mail. § 3: "a staging box quietly swallowing mail is the failure
#: mode this line exists to prevent."
REDIRECT_BANNER = (
    '*** SAM NOTIFY REDIRECT — this message was addressed to {intended} '
    'and was rerouted here by NOTIFY_REDIRECT_TO. It was NOT delivered to '
    'its subject. ***'
)

OnResult = Callable[[DeliveryResult], None]


class Notifier:
    """Renders, guards, delivers and records notifications.

    Args:
        config: overrides :meth:`NotifyConfig.from_environment`. Tests pass
            one; production does not.
        transport: overrides ``NOTIFY_TRANSPORT``. Tests pass a
            :class:`~sam.notify.transports.null.NullTransport`.
        renderer: overrides the default template directory.
        ledger: a ledger recorder, or ``None`` for "record nothing". ``None``
            is a legitimate configuration — ``preview()`` uses it, and so does
            any caller with no database to hand.
    """

    def __init__(self, *, config: Optional[NotifyConfig] = None,
                 transport: Optional[Transport] = None,
                 renderer: Optional[TemplateRenderer] = None,
                 ledger=None) -> None:
        self.config = config or NotifyConfig.from_environment()
        self.renderer = renderer or TemplateRenderer()
        self._transport = transport
        self.ledger = ledger

    # ------------------------------------------------------------- transport
    @property
    def transport(self) -> Transport:
        """The transport, built on first use from ``NOTIFY_TRANSPORT``."""
        if self._transport is None:
            self._transport = build_transport(self.config.transport, self.config)
        return self._transport

    def check(self) -> tuple[bool, Optional[str]]:
        """Probe the transport without delivering. For the admin card."""
        return self.transport.check()

    # --------------------------------------------------------------- preview
    def preview(self, message: Message) -> RenderedMessage:
        """Render without sending, guarding, or recording.

        Used by the XRAS preview modal and by ``--dry-run``. **Writes no
        ledger row** — a preview is not an attempt, and a stray ``suppressed``
        row would poison the dedup query for the real send that follows.
        """
        get_kind(message.kind)          # validate before rendering
        return self.renderer.render(message)

    # ------------------------------------------------------------------ send
    def send(self, message: Message, *, force: bool = False) -> DeliveryResult:
        """Send one message. Equivalent to :meth:`send_many` of one."""
        return self.send_many([message], force=force)[0]

    def send_many(self, messages: Iterable[Message], *,
                  force: bool = False,
                  chunk_size: Optional[int] = None,
                  on_result: Optional[OnResult] = None) -> List[DeliveryResult]:
        """Send a batch, opening one transport connection per chunk.

        Args:
            messages: what to send. Each is one person's copy.
            force: skip the suppression check. The operator's escape hatch.
            chunk_size: reconnect every this many *delivered* messages.
                ``None`` — the default — is one chunk covering the whole
                batch, which is byte-for-byte what this method did before
                chunking existed. Every existing caller gets that.
            on_result: called after each message with its
                :class:`~sam.notify.base.DeliveryResult`. This is the seam
                that keeps ``rich`` out of ``sam/`` while the CLI keeps its
                progress bar.

        Returns:
            One result per input message, in order. Never raises for a
            delivery failure — a failed send is a ``failed`` result, because
            a route that 500s on a relay hiccup is worse than one that says
            "nothing was sent".

        **Why chunk at all.** A 500-message expiration run holds one SMTP
        connection for its whole duration, and `ndir.ucar.edu` is entitled to
        drop it — after which every remaining message fails with no way back,
        because the connection is only opened once. Chunking gives the run a
        fresh connect every ``chunk_size`` messages, so a mid-run drop costs
        at most one chunk.

        The cost is paid by a relay that is *hard* down: the batch makes
        ``ceil(N / chunk_size)`` connect attempts instead of one, each
        bounded by ``mail_timeout``. At 2500/250 with the default 10 s that
        is ~100 s before the run gives up — slower than failing once, and
        deliberately so, since the case worth optimizing is the relay that
        comes back.
        """
        messages = list(messages)
        for message in messages:
            get_kind(message.kind)      # fail fast, before any side effect

        # Guards that need no transport are resolved first, so a batch that
        # is entirely suppressed never opens a connection. `None` here means
        # "not decided yet — this one goes to the transport".
        results: List[Optional[DeliveryResult]] = [
            self._pre_transport_guard(message, force=force)
            for message in messages
        ]
        pending = [i for i, result in enumerate(results) if result is None]

        if pending:
            size = len(pending) if not chunk_size or chunk_size < 1 else chunk_size
            for start in range(0, len(pending), size):
                self._send_chunk(pending[start:start + size], messages, results)

        final = [r for r in results if r is not None]
        if on_result:
            for result in final:
                on_result(result)
        return final

    def _send_chunk(self, indices: List[int], messages: List[Message],
                    results: List[Optional[DeliveryResult]]) -> None:
        """Deliver one chunk on its own connection, writing into ``results``.

        ⚠️ ``open()``/``close()`` and their ``try/finally`` live **inside**
        this method rather than around the chunk loop in :meth:`send_many`.
        A ``finally`` wrapped around the loop would leave the previous
        chunk's connection open while the next one connected — which is the
        connection leak chunking was supposed to avoid, arrived at by way of
        making the code look tidier.
        """
        transport = self.transport
        opened = False
        try:
            try:
                transport.open()
                opened = True
            except TransportError as exc:
                # This CHUNK fails identically — not the batch. Record each
                # one, so the ledger explains every recipient rather than the
                # first and a silence. The next chunk still gets a fresh
                # connect, which is the whole point of the split.
                for i in indices:
                    results[i] = self._record(messages[i], status='failed',
                                              detail=str(exc))
                return

            for i in indices:
                results[i] = self._deliver_one(messages[i], transport)
        finally:
            if opened:
                transport.close()

    # ---------------------------------------------------------------- guards
    def _pre_transport_guard(self, message: Message, *,
                             force: bool) -> Optional[DeliveryResult]:
        """Return a terminal result, or ``None`` to proceed to the transport."""
        if not self.config.enabled:
            logger.info('notify: disabled; suppressing kind=%s to=%s',
                        message.kind, message.recipient.address)
            return self._record(
                message, status='suppressed',
                detail='notifications are disabled (NOTIFY_ENABLED)')

        if not force and message.dedup_key and self.ledger is not None:
            if self.ledger.already_sent(message.dedup_key):
                logger.info('notify: suppressed by dedup_key=%s',
                            message.dedup_key)
                return self._record(
                    message, status='suppressed',
                    detail=f'already sent under {message.dedup_key}')
        return None

    def _redirected(self, message: Message) -> Message:
        """Apply ``NOTIFY_REDIRECT_TO``, returning the message to actually send.

        ⚠️ ``dedup_key`` is **not** rebuilt. It was built by the caller from
        the intended recipient, and must stay that way: a key rebuilt after
        the rewrite collapses a whole staging run onto one key, so the second
        project suppresses against the first and suppression behaves
        differently in staging than in production — defeating the point of
        having a staging mode. See § 5.
        """
        address, intended = self.config.resolve_recipient(
            message.recipient.address)
        if intended is None:
            return message
        return replace(
            message,
            recipient=replace(message.recipient, address=address),
            intended_recipient=intended,
        )

    def _banner(self, rendered: RenderedMessage,
                intended: str) -> RenderedMessage:
        """Prepend the redirect banner to both body variants."""
        banner = REDIRECT_BANNER.format(intended=intended)
        return replace(
            rendered,
            text=f'{banner}\n\n{rendered.text}',
            html=(f'<p><strong>{banner}</strong></p>\n{rendered.html}'
                  if rendered.html else None),
        )

    # --------------------------------------------------------------- deliver
    def _deliver_one(self, message: Message,
                     transport: Transport) -> DeliveryResult:
        outgoing = self._redirected(message)

        try:
            rendered = self.renderer.render(outgoing)
        except Exception as exc:
            logger.warning('notify: render failed for kind=%s to=%s: %s',
                           message.kind, message.recipient.address, exc)
            return self._record(outgoing, status='failed',
                                detail=f'render failed: {exc}')

        if outgoing.intended_recipient:
            rendered = self._banner(rendered, outgoing.intended_recipient)

        # queued FIRST, so a crash between here and the outcome leaves an
        # honest "we do not know" rather than a silent loss. The staleness
        # horizon in the ledger is what keeps that row from suppressing its
        # own retry forever.
        #
        # ⚠️ And if it cannot be written, we do NOT send. The ledger is what
        # makes a re-run safe; an unrecorded send is one the next run sends
        # again, which is the re-email bug this whole design exists to fix.
        # Fail-closed here matches NOTIFY_ENABLED's default one layer up.
        try:
            result = self._record(outgoing, status='queued', rendered=rendered,
                                  strict=True)
        except LedgerError as exc:
            logger.error('notify: refusing to send to %s — ledger unavailable: %s',
                         outgoing.recipient.address, exc)
            return DeliveryResult(
                ok=False, status='failed', message=outgoing,
                detail=f'ledger unavailable, refusing to send: {exc}')

        try:
            transport.deliver(outgoing, rendered)
        except TransportError as exc:
            return self._resolve(result, outgoing, status='failed',
                                 detail=str(exc))
        except Exception as exc:        # a transport bug, not a relay refusal
            logger.exception('notify: transport %s raised for to=%s',
                             transport.name, outgoing.recipient.address)
            return self._resolve(result, outgoing, status='failed',
                                 detail=f'{type(exc).__name__}: {exc}')

        status = 'redirected' if outgoing.intended_recipient else 'sent'
        return self._resolve(result, outgoing, status=status, detail=None)

    # ---------------------------------------------------------------- ledger
    def _record(self, message: Message, *, status: str,
                detail: Optional[str] = None,
                rendered: Optional[RenderedMessage] = None,
                strict: bool = False) -> DeliveryResult:
        """Write one ledger row, if a ledger is configured.

        Args:
            strict: propagate :class:`LedgerError` instead of swallowing it.
                True only for the ``queued`` write that precedes a send —
                the one row whose absence would let the next run re-send.
                For a ``suppressed`` or ``failed`` row nothing went out, so
                an unwritable ledger is a bookkeeping problem and must not
                turn "we sent nothing" into a raised exception.
        """
        log_id = None
        if self.ledger is not None:
            try:
                log_id = self.ledger.record(
                    message, status=status, detail=detail,
                    transport=self.config.transport, rendered=rendered)
            except LedgerError:
                if strict:
                    raise
                logger.warning('notify: could not record %r for %s; nothing '
                               'was sent, so continuing', status,
                               message.recipient.address)
        return DeliveryResult(
            ok=status in ('sent', 'redirected', 'suppressed'),
            status=status, message=message, detail=detail, log_id=log_id)

    def _resolve(self, queued: DeliveryResult, message: Message, *,
                 status: str, detail: Optional[str]) -> DeliveryResult:
        """Close out a ``queued`` row with its outcome.

        The one permitted transition on an otherwise append-only table: this
        is the row's *own* result, not a state overwrite.
        """
        if self.ledger is not None and queued.log_id is not None:
            self.ledger.resolve(queued.log_id, status=status, detail=detail)
        return DeliveryResult(
            ok=status in ('sent', 'redirected'), status=status,
            message=message, detail=detail, log_id=queued.log_id)
