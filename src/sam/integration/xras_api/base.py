"""Exceptions for the outbound XRAS Allocations API client.

The three-outcome model is copied deliberately from
``sam.integration.awards.base``, because every consumer downstream — the
worklist query, the card, the CLI's exit codes — branches on it:

* a value            -> XRAS answered, and this is the answer
* ``None``           -> XRAS answered, and there is no such thing
* an exception       -> we could not ask

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


class XrasWriteNotConfigured(XrasApiNotConfigured):
    """``XRAS_WRITE_ENABLED`` is off, or the read lever/key is missing.

    A *subclass* of the read-side not-configured error for the same reason
    that one subclasses :class:`XrasSourceUnavailable`: a route that already
    degrades on "could not ask" degrades correctly on "may not write" without
    a second branch. Routes that want to say something more specific — the
    remediation modals do — catch this first.
    """


class XrasWriteRejected(XrasSourceUnavailable):
    """XRAS reached us, understood the write, and refused it.

    Distinct from :class:`XrasSourceUnavailable` because the operator can act
    on it and a retry cannot fix it:

    * **401** — ``XA-USER`` holds no role on that request. Every request-scoped
      write authorizes this way; the fix is impersonating a role-holder,
      preferably the PI.
    * **400** — validation failed. :attr:`errors` carries XRAS's own list,
      which is what the re-submit modal renders.
    * **404** — the route accepted us and the *target* did not resolve.

    ``status`` and ``errors`` are attributes rather than message text because
    the modals branch on them.
    """

    def __init__(self, message: str, *, status: int = 0, errors=None) -> None:
        super().__init__(message)
        self.status = status
        self.errors = list(errors or ())
