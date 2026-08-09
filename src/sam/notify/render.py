"""``TemplateRenderer`` — a standalone Jinja2 environment for notifications.

Two things differ from the predecessor at
``src/cli/notifications/email.py:32-61``, both deliberate.

**The facility fallback is a constant, not a filesystem trick.** Today an
unmatched facility falls back to ``expiration.txt``, which *is*
``expiration-UNIV.txt`` — via a symlink. That works from an editable install
and does not survive a wheel build, and nothing in the source says which
facility the generic file represents. :data:`DEFAULT_FACILITY_TEMPLATE` says
it in one line.

**The environment gets ``sam.fmt``'s filters.** ``register_jinja_filters``
wrote ``app.jinja_env``, so a standalone ``Environment`` had none of them.
It now takes either an app or an environment.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 4.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import jinja2
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sam import fmt
from sam.notify.base import Message, NotifyError, RenderedMessage
from sam.notify.kinds import get_kind

logger = logging.getLogger(__name__)


#: What the deleted ``expiration.txt -> expiration-UNIV.txt`` symlinks meant.
#: An unmatched facility renders the UNIV variant, and now says so.
DEFAULT_FACILITY_TEMPLATE = 'UNIV'

#: Templates ship inside the package; see ``[tool.setuptools.package-data]``.
TEMPLATE_DIR = Path(__file__).parent / 'templates'


class TemplateError(NotifyError):
    """No text template could be resolved for a message."""


class TemplateRenderer:
    """Renders a :class:`~sam.notify.base.Message` into text and optional HTML.

    Args:
        template_dir: overrides :data:`TEMPLATE_DIR`. Tests pass a tmp_path;
            nothing in production does.
    """

    def __init__(self, template_dir: Optional[Path] = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else TEMPLATE_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            # Autoescape the HTML variant only. The text variant must stay
            # literal: a project title containing '&' belongs in a plain-text
            # mail as '&', not '&amp;'.
            autoescape=select_autoescape(enabled_extensions=('html',),
                                         default_for_string=False),
        )
        fmt.register_jinja_filters(self.env)

    # ------------------------------------------------------------ resolution
    def variants(self, message: Message) -> list[str]:
        """The template *stems* tried, in order, for one message.

        ``{base}-{facility}`` → ``{base}-UNIV`` → ``{base}``. The last step is
        what lets a kind opt out of facility variants entirely (it ships one
        bare file); the middle step is what the symlinks used to do.
        """
        kind = get_kind(message.kind)
        base = kind.template_base
        stems: list[str] = []
        if kind.facility_aware:
            if message.facility:
                stems.append(f'{base}-{message.facility}')
            stems.append(f'{base}-{DEFAULT_FACILITY_TEMPLATE}')
        stems.append(base)
        # dict.fromkeys: order-preserving dedupe, for facility == UNIV.
        return list(dict.fromkeys(stems))

    def candidates(self, message: Message, extension: str) -> list[str]:
        """:meth:`variants` with an extension applied. For error messages."""
        return [f'{stem}.{extension}' for stem in self.variants(message)]

    def resolve(self, message: Message) -> Optional[str]:
        """The **stem** whose text template exists, or ``None``.

        ⚠️ Resolution picks a *variant*, not a file per extension. Choosing
        the two independently lets the fallback split them: a WNA message
        whose facility ships ``expiration-WNA.txt`` but no ``.html`` would
        render WNA text beside **UNIV** HTML — and the HTML part is what
        almost every mail client actually displays, so the recipient would
        see the wrong facility's letter with no sign anything had gone wrong.
        Text is the required half, so it selects the variant and HTML is
        taken from that same variant or not at all.
        """
        for stem in self.variants(message):
            if self._exists(f'{stem}.txt'):
                return stem
        return None

    def _exists(self, name: str) -> bool:
        try:
            self.env.get_template(name)
            return True
        except jinja2.TemplateNotFound:
            return False

    # --------------------------------------------------------------- render
    def render(self, message: Message) -> RenderedMessage:
        """Render text (required) and HTML (optional).

        Raises:
            TemplateError: when no text template resolves. HTML absence is
                not an error — a text-only kind is a supported shape, and the
                predecessor treated it the same way.
        """
        stem = self.resolve(message)
        if stem is None:
            raise TemplateError(
                f'no text template for kind {message.kind!r} '
                f'(facility={message.facility!r}); tried '
                f'{", ".join(self.candidates(message, "txt"))} under '
                f'{self.template_dir}')
        text_name = f'{stem}.txt'

        context = dict(message.context)
        # The caller's subject wins, but templates get it too so a body can
        # echo it without the caller passing it twice.
        context.setdefault('subject', message.subject)
        context.setdefault('recipient', message.recipient.address)
        context.setdefault('recipient_name', message.recipient.name)
        context.setdefault('recipient_role', message.recipient.role)

        text = self.env.get_template(text_name).render(**context)

        # Same variant as the text, never a different facility's — see
        # :meth:`resolve`. A kind with no HTML at all is a supported shape.
        html = None
        html_name = f'{stem}.html'
        if self._exists(html_name):
            html = self.env.get_template(html_name).render(**context)
        else:
            html_name = None

        return RenderedMessage(
            subject=message.subject,
            text=text,
            html=html,
            template_text=text_name,
            template_html=html_name if html is not None else None,
        )
