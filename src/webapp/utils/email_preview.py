"""The webapp's one entry point for previewing outgoing mail.

Every preview goes through a read-only :class:`~sam.notify.Notifier`, so it
shows what the send would deliver (redirect, addressing, ``NOTIFY_BCC``) and
can never record or send. Previews always answer 200: htmx does not swap an
error response, so a problem renders as an info or error panel in the pane.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from flask import render_template, request

from webapp.utils.notify import get_notifier

PANE_TEMPLATE = 'dashboards/fragments/email_preview_pane.html'


def email_preview_context(messages: Sequence, *, id_prefix: str, pane_id: str,
                          picker_url: Optional[str] = None,
                          picker_method: str = 'post',
                          picker_include: Optional[str] = None,
                          notes: Iterable[str] = (),
                          empty: Optional[str] = None,
                          mode_banner: bool = True) -> dict:
    """The pane template's context for ``messages``, selected by ``preview_recipient``.

    ``mode_banner=False`` is for a host that already shows the disabled or
    redirected banner above the pane.
    """
    raw = request.values.get('preview_recipient') or None
    selected = int(raw) if raw and raw.isdigit() else raw
    p = get_notifier(read_only=True).preview_delivery(messages, selected=selected)
    return {'p': p, 'id_prefix': id_prefix, 'pane_id': pane_id,
            'picker_url': picker_url, 'picker_method': picker_method,
            'picker_include': picker_include, 'notes': list(notes),
            'empty': empty or 'Nothing to preview: no recipient on file.',
            'mode_banner': mode_banner}


def render_email_preview(messages: Sequence, **kw) -> str:
    """Render the preview pane for ``messages``; see :func:`email_preview_context`."""
    return render_template(PANE_TEMPLATE, **email_preview_context(messages, **kw))


def render_preview_info(text: str, level: str = 'info') -> str:
    """A pane holding only a message, for "nothing to preview" answers."""
    return render_template(PANE_TEMPLATE, p=None, info=text, level=level)
