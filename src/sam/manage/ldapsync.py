"""Write side of the LDAP sync API: upserts pushed by ``sam-ldap-syncd``.

SAM never originates identities; this module mirrors them. Every rule, and each
deliberate deviation from legacy Java SAM, is in ``docs/plans/LDAP_SYNC_API.md``.
"""

import logging

logger = logging.getLogger(__name__)


class SyncValidationError(ValueError):
    """A payload SAM rejects; the API answers 400 with legacy's message text."""

    def __init__(self, *messages: str):
        super().__init__('; '.join(messages))
        self.messages = list(messages)
