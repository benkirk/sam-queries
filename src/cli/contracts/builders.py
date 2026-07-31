"""Data extraction for contract CLI output. No Rich, no I/O.

Turns the ORM-bearing findings from :mod:`sam.queries.contract_audit` into the
plain-dict envelope that feeds both ``output_json()`` and
``display_contract_audit()`` — one payload, two renderers, so ``--format json``
can never drift from the Rich report.
"""

from sam.queries.contract_audit import CHECKS


def contract_dict(contract):
    """Serialize one contract via the shared Summary-tier schema.

    Imported lazily: ``sam.schemas`` pulls in ``webapp.extensions`` at module
    level, and only this one command needs it.  ``dump()`` never touches
    ``BaseSchema.Meta.sqla_session``, so it works with no Flask app context.

    Public rather than ``_``-prefixed because ``cli/awards/`` reuses it: an
    award cross-reference renders the SAM contract it matched, and both
    commands must describe a contract the same way.
    """
    from sam.schemas import ContractSummarySchema
    return ContractSummarySchema().dump(contract)


def build_contract(contract, *, projects=True) -> dict:
    """Assemble the ``contract`` envelope for one contract.

    Args:
        contract: a ``Contract``, ideally loaded via ``get_contract_detail``
            so the project chain is warm.
        projects: include the linked-project list.  ``contract.projects``
            holds ``ProjectContract`` association rows, so this hops through
            ``.project``.
    """
    payload = {'kind': 'contract', **contract_dict(contract)}

    if projects:
        payload['projects'] = sorted(
            ({'projcode': link.project.projcode,
              'title':    link.project.title,
              'is_active': bool(link.project.is_active)}
             for link in contract.projects if link.project is not None),
            key=lambda p: p['projcode'])

    return payload


def build_contract_search(contracts, *, pattern=None, filters=None,
                          scope='open') -> dict:
    """Assemble the ``contract_search_results`` envelope.

    Mirrors ``user_search_results`` / ``project_search_results``: the search
    terms are echoed back so a JSON consumer can tell an empty result from a
    query it did not send.
    """
    return {
        'kind':      'contract_search_results',
        'pattern':   pattern,
        'scope':     scope,
        'filters':   {k: v for k, v in (filters or {}).items() if v},
        'count':     len(contracts),
        'contracts': [contract_dict(c) for c in contracts],
    }


def build_contract_audit(findings, program_findings, *, scope,
                         contracts_audited, source_check=None) -> dict:
    """Assemble the ``contract_audit`` envelope.

    Args:
        findings:         output of ``audit_contracts()``.
        program_findings: output of ``audit_nsf_programs()``.
        scope:            ``'open'`` or ``'all'``.
        contracts_audited: how many contracts were examined.
        source_check:     output of :func:`build_source_check`, or ``None``
                          when ``--check-sources`` was not requested.

    Returns:
        The complete payload.  **Every check appears**, including ones with no
        findings — an absent section reads as "not run" rather than "clean".
    """
    by_check = {}
    for finding in findings:
        by_check.setdefault(finding['check'], []).append(finding)

    checks = []
    for key, label, severity in CHECKS:
        hits = by_check.get(key, [])
        checks.append({
            'key':      key,
            'label':    label,
            'severity': severity,
            'count':    len(hits),
            'findings': [{'contract': contract_dict(f['contract']),
                          'detail':   f['detail']} for f in hits],
        })

    return {
        'kind':              'contract_audit',
        'scope':             scope,
        'contracts_audited': contracts_audited,
        'checked_sources':   source_check is not None,
        'total_findings':    len(findings) + len(program_findings),
        'checks':            checks,
        'program_findings':  program_findings,
        'source_check':      source_check,
    }


def build_source_check(results) -> dict:
    """Summarize the ``--check-sources`` pass.

    Args:
        results: ``(contract, comparison)`` pairs, where *comparison* is a
            :func:`sam.integration.awards.audit.compare_contract` result.

    Returns:
        Counts plus the per-contract detail for everything that is **not** a
        clean agreement.  Contracts where SAM matches the source are counted
        and dropped: they are the expected case and would bury the rest.
    """
    counts = {'checked': 0, 'unchecked': 0, 'no_record': 0,
              'suspect_match': 0, 'divergent': 0, 'agreed': 0}
    contracts = []

    for contract, comparison in results:
        status = comparison['status']
        if status == 'unavailable':
            counts['unchecked'] += 1
        elif status == 'no_record':
            counts['no_record'] += 1
            counts['checked'] += 1
        elif status == 'suspect_match':
            counts['suspect_match'] += 1
            counts['checked'] += 1
        else:
            counts['checked'] += 1
            if comparison['divergences']:
                counts['divergent'] += 1
            elif not comparison['hints']:
                counts['agreed'] += 1
                continue

        contracts.append({
            'contract':       contract_dict(contract),
            'status':         status,
            'provenance':     comparison.get('provenance'),
            'divergences':    comparison['divergences'],
            'hints':          comparison['hints'],
            'source_summary': comparison.get('source_summary'),
        })

    return {**counts, 'contracts': contracts}
