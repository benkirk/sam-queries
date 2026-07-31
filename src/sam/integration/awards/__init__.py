"""Award-source lookup: prefill contract fields from public funding APIs.

Two entry points, and the difference matters:

* :func:`resolve_award` takes a **number** and returns one full record.
* :func:`search_awards` takes **free text** and returns ``(records, errors)``
  — summaries, because USAspending's program name is detail-only. A chosen
  hit is chained back through ``resolve_award`` to fill it in.

:func:`resolve_person` maps a provider's PI/program-officer onto a SAM user.
See docs/plans/CONTRACT_IMPORTING_PLAN.md for the source survey behind the
two-provider design.
"""

from sam.integration.awards.base import (
    AwardProvider,
    AwardRecord,
    AwardSourceUnavailable,
    PersonRef,
    UNAVAILABLE_FIELD_LABELS,
)
from sam.integration.awards.client import AwardHttpClient
from sam.integration.awards.nsf import NsfAwardProvider, nsf_award_id
from sam.integration.awards.people import resolve_person
from sam.integration.awards.registry import (
    build_providers,
    providers,
    providers_for,
    resolve_award,
    search_awards,
    search_providers,
)
from sam.integration.awards.usaspending import (
    UsaSpendingProvider,
    award_id_candidates,
)

__all__ = [
    'AwardHttpClient',
    'AwardProvider',
    'AwardRecord',
    'AwardSourceUnavailable',
    'NsfAwardProvider',
    'PersonRef',
    'UNAVAILABLE_FIELD_LABELS',
    'UsaSpendingProvider',
    'award_id_candidates',
    'build_providers',
    'nsf_award_id',
    'providers',
    'providers_for',
    'resolve_award',
    'resolve_person',
    'search_awards',
    'search_providers',
]
