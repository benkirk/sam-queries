"""Provider name -> class, and the ``TICKET_PROVIDER`` selector.

A dict, like ``sam.notify.registry``: a typo in ``TICKET_PROVIDER`` fails with
the valid names in the message. ``''``/``mail``/``none`` mean Jira is not in
play at all (no create, no learn); pausing creates while still learning is
``JIRA_WRITE_ENABLED=0``. Imports ``requests`` via ``jira``, so callers import
this module by path, never through ``sam.integration.tickets``.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Type

from sam.integration._config import config_str
from sam.integration.tickets.base import TicketNotConfigured, TicketProvider
from sam.integration.tickets.jira import JiraServiceDeskProvider

PROVIDERS: Dict[str, Type[TicketProvider]] = {
    JiraServiceDeskProvider.name: JiraServiceDeskProvider,
}

MAIL_NAMES: FrozenSet[str] = frozenset({'', 'mail', 'none'})


def provider_names() -> list:
    return sorted(PROVIDERS)


def build_provider(name: str, *, interactive: bool = False) -> Optional[TicketProvider]:
    """The named provider, or ``None`` for a mail name.

    Raises:
        TicketNotConfigured: an unknown name, listing the valid ones.
    """
    key = (name or '').strip().lower()
    if key in MAIL_NAMES:
        return None
    try:
        cls = PROVIDERS[key]
    except KeyError:
        raise TicketNotConfigured(
            f'unknown TICKET_PROVIDER {name!r}; expected one of '
            f'{", ".join(sorted(MAIL_NAMES | set(PROVIDERS)))}') from None
    return cls.from_environment(interactive=interactive)


def provider_from_environment(*, interactive: bool = False) -> Optional[TicketProvider]:
    """``TICKET_PROVIDER``, built. See :func:`build_provider`."""
    return build_provider(config_str('TICKET_PROVIDER', ''), interactive=interactive)


def provider_for_stored(name: str) -> Optional[TicketProvider]:
    """The provider a stored row names, for ``browse_url``; ``None`` if retired."""
    cls = PROVIDERS.get(name or '')
    return cls.from_environment() if cls else None
