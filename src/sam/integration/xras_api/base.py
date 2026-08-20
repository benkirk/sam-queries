"""Exceptions for the outbound XRAS Allocations API client.

The three-outcome model is copied deliberately from
``sam.integration.awards.base``, because every consumer downstream — the
worklist query, the card, the CLI's exit codes — branches on it:

* a value            → XRAS answered, and this is the answer
* ``None``           → XRAS answered, and there is no such thing
* an exception       → we could not ask

Collapsing the last two is the bug this split exists to prevent: "no such
person" and "XRAS is down" look identical in a ``try: ... except: return
None`` and mean opposite things to an operator working the account-creation
worklist.
"""

from __future__ import annotations


class XrasSourceUnavailable(Exception):
    """The XRAS API could not be reached, or refused the request.

    Distinct from a lookup returning ``None``, which means the API was
    reached and holds no such record.
    """


class XrasApiNotConfigured(XrasSourceUnavailable):
    """No API key, or ``XRAS_OUTGOING_ENABLED`` is off.

    A *subclass* on purpose. An unconfigured deployment is the shipped
    state (see ``helm/values.yaml``), and every call site already handles
    "could not ask" — so it degrades through exactly the same path as a
    timeout instead of needing a second branch everywhere.
    """
