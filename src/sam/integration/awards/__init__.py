"""Award-source lookup: prefill contract fields from public funding APIs.

Entry point is :func:`resolve_award`; :func:`resolve_person` maps a
provider's PI/program-officer onto a SAM user. See
docs/plans/CONTRACT_IMPORTING_PLAN.md for the source survey behind the
two-provider design.
"""

from sam.integration.awards.base import (
    AwardProvider,
    AwardRecord,
    AwardSourceUnavailable,
    PersonRef,
)
from sam.integration.awards.client import AwardHttpClient
from sam.integration.awards.nsf import NsfAwardProvider, nsf_award_id
from sam.integration.awards.people import resolve_person
from sam.integration.awards.registry import (
    providers,
    providers_for,
    resolve_award,
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
    'UsaSpendingProvider',
    'award_id_candidates',
    'nsf_award_id',
    'providers',
    'providers_for',
    'resolve_award',
    'resolve_person',
]
