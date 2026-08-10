"""Transport name → factory.

A dict rather than entry points or import-by-string: the set is three, they
all ship in this package, and a typo in ``NOTIFY_TRANSPORT`` should fail with
the valid names in the message rather than an ``ImportError``.
"""

from __future__ import annotations

from typing import Callable, Dict, Mapping

from sam.notify.base import NotifyError, Transport
from sam.notify.config import NotifyConfig
from sam.notify.transports import (
    ConsoleTransport, NullTransport, SmtpTransport,
)

#: Every selectable transport.
TRANSPORTS: Mapping[str, Callable[[NotifyConfig], Transport]] = {
    SmtpTransport.name: SmtpTransport,
    NullTransport.name: NullTransport,
    ConsoleTransport.name: ConsoleTransport,
}


def build_transport(name: str, config: NotifyConfig) -> Transport:
    """Instantiate the named transport.

    Raises:
        NotifyError: on an unknown name. Deliberately **not** a silent
            fallback to ``null``: a deployment that meant to send and typed
            the name wrong must fail loudly, not quietly record
            ``NULL`` rows that look like a working system.
    """
    try:
        factory = TRANSPORTS[name]
    except KeyError:
        raise NotifyError(
            f'unknown NOTIFY_TRANSPORT {name!r}; expected one of '
            f'{", ".join(sorted(TRANSPORTS))}') from None
    return factory(config)


def transport_names() -> Dict[str, str]:
    """``{name: channel}`` — for the admin card and for tests."""
    return {name: factory.channel.value for name, factory in TRANSPORTS.items()}
