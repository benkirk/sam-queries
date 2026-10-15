"""``dry-run``: a provider that files nowhere. It logs each ticket and comment and
mints ``DRY-<handle>`` as the key, so a deployment with no tracker (samuel-dev)
runs the whole filing path, links and ledger rows included, without posting.

Not in ``registry.PROVIDERS``: the hourly learn and refresh read every provider
there, and this one has nothing to read. It files from tasks only; the webapp's
interactive build refuses it, so account tickets can never land here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sam.integration.tickets.base import (TicketDraft, TicketNotConfigured,
                                          TicketProvider, TicketRef)

logger = logging.getLogger(__name__)

KEY_PREFIX = 'DRY-'


class DryRunProvider(TicketProvider):
    name = 'dry-run'

    @classmethod
    def from_environment(cls, *, interactive: bool = False) -> 'DryRunProvider':
        if interactive:
            raise TicketNotConfigured('dry-run files from scheduled tasks only')
        return cls()

    @property
    def configured(self) -> bool:
        return True

    @property
    def write_configured(self) -> bool:
        return True

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, 'configured': True, 'write_configured': True}

    def create(self, draft: TicketDraft) -> TicketRef:
        key = f'{KEY_PREFIX}{draft.handle}'
        logger.info('dry-run: would file %s (%s) as %s\n%s\n%s',
                    draft.handle, draft.kind, key, draft.summary, draft.body)
        self.post_automation_note(key, draft)
        return TicketRef(key, status='dry-run')

    def comment(self, key: str, text: str, *, internal: bool = True) -> None:
        logger.info('dry-run: would comment on %s (%s)\n%s',
                    key, 'internal' if internal else 'public', text)

    def get(self, key: str) -> Optional[TicketRef]:
        return None

    def browse_url(self, key: str) -> str:
        return ''
