"""Data extraction for award CLI output. No Rich, no I/O.

Turns :class:`~sam.integration.awards.AwardRecord` objects into the plain-dict
envelopes that feed both ``output_json()`` and ``cli.awards.display`` — one
payload, two renderers, so ``--format json`` can never drift from the Rich
report.

Two things every award payload must carry, because they are the difference
between a useful answer and a misleading one:

* ``provenance`` per record. NSF and USAspending disagree about what they can
  supply, so a row without its source is uninterpretable.
* ``unavailable`` as an explicit **positive** note rather than a blank.
  USAspending structurally has no PI or program officer (FFATA/DATA Act does
  not collect them), and "cannot supply" is a different statement from
  "happens to be empty".
"""

from cli.contracts.builders import contract_dict

#: ``AwardRecord.unavailable_fields`` -> what the operator reads. Ordered as
#: a person would say it ("PI and Monitor"), not alphabetically.
_UNAVAILABLE_LABELS = {'pi': 'PI', 'monitor': 'Monitor'}


def _person(person) -> dict:
    """A ``PersonRef`` as a dict, or ``None``."""
    if not person:
        return None
    return {'name': person.name, 'email': person.email,
            'label': person.label}


def award_dict(record) -> dict:
    """Serialize one :class:`AwardRecord`.

    ``unavailable`` is rendered as a sorted list of human labels alongside the
    raw ``unavailable_fields``, so a JSON consumer gets both the machine keys
    and the sentence a person needs.
    """
    present = set(record.unavailable_fields or ())
    # Canonical order first, then anything a future provider adds.
    unavailable = ([f for f in _UNAVAILABLE_LABELS if f in present]
                   + sorted(present - set(_UNAVAILABLE_LABELS)))
    return {
        'provenance':      record.provenance,
        'contract_number': record.contract_number,
        'title':           record.title,
        'start_date':      record.start_date,
        'end_date':        record.end_date,
        'url':             record.url,
        'program_name':    record.program_name,
        'pi':              _person(record.pi),
        'monitor':         _person(record.monitor),
        'unavailable_fields': unavailable,
        'unavailable': [_UNAVAILABLE_LABELS.get(f, f) for f in unavailable],
    }


def build_award(record, *, contract_number, source=None, in_sam=None) -> dict:
    """Assemble the ``award`` envelope for a single-number lookup.

    Args:
        record: the :class:`AwardRecord` the providers returned.
        contract_number: what was asked for — echoed so a consumer can tell
            an empty answer from a query it did not send.
        source: the ``--source`` that scoped the lookup, if any.
        in_sam: the cross-reference block from :func:`build_in_sam`, or
            ``None`` when SAM has no contract with this number.
    """
    return {
        'kind':            'award',
        'contract_number': contract_number,
        'source':          source,
        'award':           award_dict(record),
        'in_sam':          in_sam,
    }


def build_in_sam(contract, comparison) -> dict:
    """Cross-reference one award against the SAM contract of the same number.

    *comparison* is a
    :func:`sam.integration.awards.audit.compare_contract` result, so this
    inherits #403's four noise rules **and** the ``suspect_match`` guard.

    That guard is why this block exists in this shape rather than as a flat
    field-by-field diff: ``sam-search awards 014421 --source DOD`` resolves to
    a 2009 award titled "MEALS", and the output has to say the provider
    probably found a different award instead of presenting its values as this
    contract's.
    """
    return {
        'contract':       contract_dict(contract),
        'status':         comparison['status'],
        'divergences':    comparison['divergences'],
        'hints':          comparison['hints'],
        'source_summary': comparison.get('source_summary'),
    }


def build_award_search(records, errors, *, query, limit,
                       sources=None, known=None) -> dict:
    """Assemble the ``award_search_results`` envelope.

    Args:
        records: the :class:`AwardRecord` list from ``search_awards``.
        errors:  its per-provider error list. **Always present**, even when
            empty: a partial result that silently looks complete is the one
            failure mode this whole return shape exists to prevent.
        query:   the search term, echoed back.
        limit:   the per-provider cap that was applied.
        sources: the ``--source`` scoping, if any.
        known:   ``{contract_number: Contract}`` for hits SAM already has.
    """
    known = known or {}

    results = []
    for record in records:
        row = award_dict(record)
        existing = known.get((record.contract_number or '').strip())
        row['in_sam'] = contract_dict(existing) if existing else None
        results.append(row)

    return {
        'kind':       'award_search_results',
        'query':      query,
        'limit':      limit,
        'sources':    list(sources) if sources else None,
        'count':      len(results),
        'already_in_sam': sum(1 for r in results if r['in_sam']),
        'results':    results,
        'errors':     errors,
    }
