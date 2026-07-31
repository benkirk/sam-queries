"""Award-source value types and the provider contract.

A *provider* wraps one public award API (NSF, USAspending, …) and answers a
single question: given a ``contract_source`` name and a ``contract_number``,
what does the funding agency say about this award?

Three distinctions are load-bearing and easy to collapse by accident:

* ``fetch()`` returning **None** means "the agency has no such award".
  Raising :class:`AwardSourceUnavailable` means "we could not ask" — a
  timeout, a 5xx, a DNS failure. The UI says very different things for
  the two, so callers must not conflate them.
* :attr:`AwardRecord.unavailable_fields` names what a provider
  *structurally cannot* supply, as opposed to what happened to be blank
  for this award. USAspending has no program-officer concept at all
  (FFATA/DATA Act does not collect it), so the form can state
  "enter Monitor manually" instead of rendering a silent blank.
* :class:`PersonRef` carries the agency's raw name/email and is
  deliberately *not* a SAM ``User``. Mapping one onto the other is
  :func:`sam.integration.awards.people.resolve_person`'s job and nothing
  else's — see docs/plans/CONTRACT_IMPORTING_PLAN.md § F2, which wants to
  move external contacts out of ``users`` eventually. Keeping the seam in
  one function makes that a change to one return type rather than a
  rewrite of the prefill path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import FrozenSet, List, Optional


class AwardSourceUnavailable(Exception):
    """The award source could not be reached (transport failure).

    Distinct from ``fetch()`` returning ``None``, which means the source
    was reached and has no such award.
    """


@dataclass(frozen=True)
class PersonRef:
    """A person as the funding agency describes them — not a SAM user."""

    name: Optional[str] = None
    email: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.name or self.email)

    @property
    def label(self) -> str:
        """Human-readable 'Name <email>' for the suggest-don't-impose hint."""
        if self.name and self.email:
            return f'{self.name} <{self.email}>'
        return self.name or self.email or ''


@dataclass(frozen=True)
class AwardRecord:
    """One award as a provider reports it, mapped onto ``contract`` columns.

    Every field is optional: providers differ in what they carry, and a
    partial record still saves the operator typing. ``provenance`` is the
    provider name, shown in the form so the operator knows where a
    prefilled value came from; nothing about it is persisted.
    """

    provenance: str
    contract_number: Optional[str] = None
    title: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    url: Optional[str] = None
    program_name: Optional[str] = None
    pi: Optional[PersonRef] = None
    monitor: Optional[PersonRef] = None
    unavailable_fields: FrozenSet[str] = field(default_factory=frozenset)


#: Human labels for :attr:`AwardRecord.unavailable_fields`, so the CLI and the
#: create form phrase the same structural gap the same way. Both render
#: "<provenance> cannot supply <labels> — enter manually"; they had grown two
#: label maps that disagreed ('PI' vs 'the PI'). A field with no entry here
#: falls back to its own key.
UNAVAILABLE_FIELD_LABELS = {'pi': 'PI', 'monitor': 'Monitor'}


class AwardProvider(ABC):
    """One public award API.

    ``supports()`` is a cheap, offline predicate — it must not do I/O, so
    the registry can pick a provider without paying for a request.
    """

    #: Provider name; becomes ``AwardRecord.provenance`` and the cache key
    #: prefix, so it must be stable and unique.
    name: str = ''

    #: The ``contract_source`` this provider is specific to, or ``None``
    #: for a generic fallback. The registry tries specific before generic.
    source: Optional[str] = None

    @abstractmethod
    def supports(self, source_name: Optional[str],
                 contract_number: Optional[str]) -> bool:
        """Whether this provider can attempt *contract_number*. No I/O."""

    @abstractmethod
    def fetch(self, contract_number: str) -> Optional[AwardRecord]:
        """Look the award up.

        Returns:
            The record, or ``None`` when the source has no such award.

        Raises:
            AwardSourceUnavailable: the source could not be reached.
        """

    def search(self, query: str, limit: int = 10) -> List[AwardRecord]:
        """Free-text search. Providers that cannot search return ``[]``.

        Concrete with an empty default rather than abstract: ``fetch`` is the
        provider contract, search is an optional capability, and a default
        keeps a future provider from having to fake one.

        Unlike :meth:`fetch`, there is no number to scope on, so
        :meth:`supports` plays no part here — the registry decides which
        providers a search fans out to.

        Returns:
            Zero or more records, newest-API-order, capped at *limit*.
            Search records are **summaries**: a provider may leave fields set
            on a fetched record blank here (USAspending's ``program_name``
            comes from a detail-only endpoint), which is why a hit is chained
            into ``fetch`` rather than used directly.

        Raises:
            AwardSourceUnavailable: the source could not be reached.
        """
        return []
