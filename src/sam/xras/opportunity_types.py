"""XRAS allocation-type/panel ids -> the SAM ``(panel, allocation_type)`` pair.

The reference half of the ``opportunityId`` map.
``xras_opportunity_allocation_type`` answers "which allocation type is *this
opportunity*"; this answers "which allocation type is *this kind of
opportunity*", which is what lets ``xras_sweep`` propose a row for an
opportunity nobody has mapped yet.

A constant rather than a table because it changes at code cadence: 42 distinct
opportunities across the whole NCAR process collapse to the eight pairs below,
and a new pair appears only when NCAR invents a new allocation product. What
churns is opportunities -- about four a year -- and those are a table the sweep
fills in itself. A unit test asserts every value here resolves to a real
``(panel, allocation_type)`` row, so a typo fails the suite rather than waiting
in production.

WARNING: this mapping is **not injective**, so XRAS does not already know the
answer the free-text ladder is guessing at:

* ``500026`` (``Educational``) covers ``Classroom Allocation (University)``
  AND the unsponsored family. In SAM those are ``Classroom`` and
  ``Small (No NSF award)`` -- different rows.
* ``(500088, 500045)`` covers NSC AND ``NCAR - ASD Opportunity``. In SAM those
  differ by *facility* (1 vs 7), which is what reaches ``next_projcode``.

Neither is recoverable from the API -- XRAS genuinely models each as one type
on one panel. Both are pinned as ``source='manual'`` rows that the sweep can
never overwrite, since it only inserts where no row exists. They are also why
the sweep writes only when this mapping and the free-text ladder AGREE: that
rule withholds both cases without needing to know about them, and withholds the
first Wyoming opportunity too, which is exactly the one a human should confirm.

Verified 2026-08-20 against ``GET /v1/opportunities/list/:ids`` and
``GET /v1/panels``; see ``docs/xras/outgoing/XRAS_OPPORTUNITY_ALLOCATION_TYPE.md``
section 8.5.
"""

from typing import Dict, Optional, Tuple

__all__ = ['XRAS_TYPE_MAP', 'XRAS_PANEL_NAMES', 'pair_for_opportunity',
           'primary_panel_id']

#: XRAS panel names, for humans reading a report. Not used for matching — the
#: ids are the stable thing, and the names are XRAS's *review-panel* vocabulary
#: rather than SAM's ``panel`` table (only ``CHAP`` even resembles a SAM name).
XRAS_PANEL_NAMES: Dict[int, str] = {
    500021: 'CISL Resource Support',
    500022: 'CISL HPC Allocation Panel',
    500032: 'External reviewers for CHAP',   # secondary on Large; never primary
    500045: 'NSC Allocation Panel',
    500046: 'Admin Panel',
}

#: ``(allocationTypeId, primary panelId)`` -> SAM ``(panel_name, allocation_type)``.
#:
#: Keyed on the pair rather than the type id alone because the panel is the half
#: this whole feature exists to get right: the free-text ladder can never produce
#: ``UW``, ``WRAP`` or ``LCAP``, so a Wyoming opportunity is invisible to it. If
#: WNA ever submits with a familiar type id on an unfamiliar panel, the pair
#: misses and the row is withheld for review rather than silently mapped to UNIV.
#:
#: The "covers" counts are opportunities observed in the full 2026-08-20
#: enumeration, and are why this is worth having: two rows carry the bulk of a
#: process that has run 42 opportunities.
XRAS_TYPE_MAP: Dict[Tuple[int, int], Tuple[str, str]] = {
    (500023, 500022): ('CHAP', 'CHAP'),                             # Large — the biggest single pair
    (500088, 500045): ('NCAR-ARP', 'NSC'),                          # NCAR Strategic Computing
    (500026, 500021): ('UNIV USS', 'Classroom'),                    # Educational — see the caveat above
    (500024, 500021): ('UNIV USS', 'Small'),                        # Small
    (500847, 500021): ('UNIV USS', 'Small (No NSF award)'),         # Exploratory
    (500848, 500021): ('UNIV USS', 'Data'),                         # Data Analysis
    (501276, 500046): ('External Projects', 'External Project'),    # NCAR External Projects
    # Historical: NSC requests were filed under the **Large** type id before
    # 500088 existed (`NCAR - NSC Allocation Request-Fall 2018`). Keying on
    # the pair rather than the type id alone is what makes this resolvable —
    # 500023 on the CHAP panel is CHAP, and on the NSC panel it is NSC.
    (500023, 500045): ('NCAR-ARP', 'NSC'),
}


def primary_panel_id(payload) -> Optional[int]:
    """The ``panelId`` XRAS marks ``isPrimary``, or ``None``.

    WARNING: **Not ``panels[0]``.** Large opportunities carry two — ``500022``
    (CISL HPC Allocation Panel) and ``500032`` (External reviewers for CHAP) —
    and only the first is the one SAM means by "panel". Order is not guaranteed,
    and the schema comment on the *inbound* ``panels[]`` says the same thing:
    "``isPrimary`` is not necessarily index 0".
    """
    for panel in (payload.get('panels') or []):
        if isinstance(panel, dict) and panel.get('isPrimary'):
            panel_id = panel.get('panelId')
            if panel_id is not None:
                return int(panel_id)
    return None


def pair_for_opportunity(payload) -> Optional[Tuple[str, str]]:
    """The SAM ``(panel, allocation_type)`` this opportunity implies, or ``None``.

    ``None`` for every incomplete or unrecognised shape — no
    ``allocationTypeInfo``, no primary panel, or a pair not in
    :data:`XRAS_TYPE_MAP`. Callers treat that as "cannot propose", never as an
    error: an unknown pair is exactly what a genuinely new allocation product
    looks like, and the right response is to report it, not to guess.
    """
    info = payload.get('allocationTypeInfo') or {}
    type_id = info.get('allocationTypeId')
    panel_id = primary_panel_id(payload)
    if type_id is None or panel_id is None:
        return None
    return XRAS_TYPE_MAP.get((int(type_id), panel_id))
