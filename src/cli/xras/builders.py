"""Payload builders for ``sam-admin xras``.

One payload, two renderers — ``display.py`` renders exactly the dict that
``--format json`` emits, so the Rich report and the JSON envelope cannot drift.
Builders never import Rich and never touch the console.

Key order in these dicts is the wire order: ``output_json`` uses
``sort_keys=False`` deliberately, so the literal order below is what a consumer
sees.
"""

from typing import Any, Dict, List, Optional

from sam.queries.xras_actions import (
    audit_opportunity_mapping,
    audit_resource_mapping,
    get_recent_xras_actions,
    propose_opportunity_mapping,
    summarize_xras_actions,
)


def build_action_list(session, *, filters: Dict[str, Any],
                      limit: Optional[int]) -> Dict[str, Any]:
    """The ``xras_action_list`` envelope: recent actions matching ``filters``."""
    rows = get_recent_xras_actions(session, **filters, limit=limit)
    return {
        'kind':      'xras_action_list',
        'count':     len(rows),
        'filters':   _describe_filters(filters),
        'limit':     limit,
        'actions':   rows,
    }


def build_action_detail(session, action_id: int, *,
                        include_payload: bool) -> Optional[Dict[str, Any]]:
    """The ``xras_action`` envelope for one action, or ``None`` if absent.

    ``include_payload`` is a flag rather than always-on because the payload is the
    request body verbatim and carries participant names, emails and phone numbers.
    A CLI operator can have it — but they have to ask, so it never lands in a
    terminal scrollback or a piped log by accident.
    """
    rows = get_recent_xras_actions(session, action_log_id=action_id,
                                   include_payload=include_payload)
    if not rows:
        return None
    action = rows[0]

    # The re-check lineage, both directions: what this re-checked, and what re-checked it.
    children = get_recent_xras_actions(session, source_action=action_id,
                                       sort_by='received_time', sort_dir='asc')

    return {
        'kind':           'xras_action',
        'action_log_id':  action_id,
        'action':         action,
        'source_action_id':   action['source_action_id'],
        'rechecks':        [{'action_log_id': c['action_log_id'],
                            'received_time': c['received_time'],
                            'status':        c['status'],
                            'processed_by':  c['processed_by']}
                           for c in children],
        'payload_included': include_payload,
    }


def build_summary(session, *, filters: Dict[str, Any]) -> Dict[str, Any]:
    """The ``xras_action_summary`` envelope: rollup by status and action type."""
    summary = summarize_xras_actions(
        session,
        action_type=filters.get('action_type'),
        status=filters.get('status'),
        start_date=filters.get('start_date'),
        end_date=filters.get('end_date'),
    )
    return {
        'kind':       'xras_action_summary',
        'total':      summary['total'],
        'filters':    _describe_filters(filters),
        # Passed through, not re-derived. `summarize_xras_actions` already seeds
        # every status at zero — an absent bucket reads as "not measured" rather
        # than "none" — AND deliberately keeps any status outside the vocabulary,
        # because that is a bug worth surfacing rather than a filter miss.
        #
        # WARNING: do NOT rewrite this as `{s: ... for s in XRAS_ACTION_STATUSES}`.
        # That re-applies the zero-fill (already done) and silently drops the stray,
        # while `total` counts it either way — so the envelope reports a total that
        # does not reconcile with the sum of its own buckets.
        'by_status':  summary['by_status'],
        'by_type':    summary['by_type'],
    }


def build_recheck_result(action_id: int, new_id: int, *, actor: str,
                        action: Dict[str, Any]) -> Dict[str, Any]:
    """The ``xras_recheck`` envelope. Only ever reached via ``--json-writes``."""
    return {
        'kind':           'xras_recheck',
        'source_action_id': action_id,
        'new_action_id':  new_id,
        'actor':          actor,
        'status':         action['status'],
        'action_type':    action['action_type'],
        'request_number': action['request_number'],
    }


def _describe_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    """Echo the active filters into the envelope.

    A consumer diffing two runs needs to know what each one asked for; a payload
    that reports counts without their scope is not reproducible.
    """
    return {
        'status':         filters.get('status'),
        'action_type':    filters.get('action_type'),
        'request_number': filters.get('request_number'),
        'start_date':     filters.get('start_date'),
        'end_date':       filters.get('end_date'),
    }


def build_mapping_report(session, *, xras_keys=None) -> dict:
    """The ``xras_resource_mapping`` envelope.

    The audit itself is :func:`sam.queries.xras_actions.audit_resource_mapping` —
    builders are ORM->dict extractors, not query modules, and the webapp should be
    able to reach the same answer without importing the CLI.

    *xras_keys* is the live catalog when one could be fetched, making the report
    two-sided; ``None`` reproduces the local-only report byte for byte.
    """
    return {'kind': 'xras_resource_mapping',
            **audit_resource_mapping(session, xras_keys=xras_keys)}


def build_opportunity_report(session, *, opportunities=None) -> dict:
    """The ``xras_opportunity_mapping`` envelope.

    Two query-layer calls, deliberately not one: :func:`audit_opportunity_mapping`
    answers *what is mapped*, and :func:`propose_opportunity_mapping` answers *what
    could be, and by whose authority*. Both live in ``sam.queries.xras_actions`` and
    are shared with ``xras_sweep``, so the CLI and the task cannot drift into two
    opinions about the same opportunity.

    WARNING: **The proposal runs over the UNMAPPED subset only**, exactly as
    ``_map_new_opportunities`` does in the sweep. Run over everything and the two
    permanent ``manual`` rows — the ones where XRAS is wrong about SAM and a human
    said so — reappear in ``review`` on every invocation, which is how an operator
    learns to ignore the bucket that matters.

    *opportunities* is the live open catalog when one could be fetched, making the
    report two-sided; ``None`` reports the local half with ``live_checked`` False to
    say so. Injected rather than fetched here for the same reason
    :func:`build_mapping_report` injects *xras_keys*.
    """
    payloads = [o for o in (opportunities or []) if isinstance(o, dict)]
    ids = [o['opportunityId'] for o in payloads if o.get('opportunityId') is not None]

    audit = audit_opportunity_mapping(
        session, opportunity_ids=ids if opportunities is not None else None)

    unmapped = set(audit['unmapped_ids'])
    proposal = propose_opportunity_mapping(
        session, [o for o in payloads if o.get('opportunityId') in unmapped])

    return {'kind': 'xras_opportunity_mapping', **audit, 'proposal': proposal}


def build_account_worklist(session, *, since=None, until=None,
                           enrich=False, max_lookups=100,
                           pending_rows=None, pending_checked=False) -> dict:
    """The ``xras_accounts`` envelope — who needs an account before a handoff.

    *enrich* is opt-in because it costs a round trip to XRAS per username. The
    enrichment report rides in the envelope rather than being folded into the
    rows: "we did not ask" and "we asked and XRAS was down" are different facts
    and a consumer diffing two runs needs to tell them apart.

    *pending_rows* is the Feed-B worklist ``xras_sweep`` published, injected by
    the caller. WARNING: ``pending_checked`` is the same distinction ``live_checked``
    draws on the mapping audit and is the reason it is a separate flag rather
    than ``pending_rows is not None``: a consumer must be able to tell "Feed B
    is empty" from "we could not read Feed B", because the second one means the
    number it is looking at is a **subset of the queue** and the first does not.
    """
    from sam.queries.xras_accounts import (enrich_worklist,
                                           get_account_worklist,
                                           stamp_waiting_days,
                                           worklist_counts)

    rows = get_account_worklist(session, since=since, until=until,
                                pending_rows=pending_rows)
    stamp_waiting_days(rows)
    enrichment = (enrich_worklist(rows, max_lookups=max_lookups)
                  if enrich else None)

    return {
        'kind': 'xras_accounts',
        'counts': worklist_counts(rows),
        'enriched': bool(enrich),
        'enrichment': enrichment,
        'pending_checked': bool(pending_checked),
        'accounts': [_account_row(r) for r in rows],
    }


def _account_row(row) -> dict:
    """One worklist row, JSON-shaped. Key order is wire order."""
    return {
        'username': row['username'],
        'classification': row['classification'],
        'remedy': row['remedy'],
        'placeholder': row['placeholder'],
        'roles': list(row['roles']),
        'is_account_to_be_created': row['is_account_to_be_created'],
        'is_reconciled': row['is_reconciled'],
        'first_seen': row['first_seen'],
        'last_seen': row['last_seen'],
        'waiting_since': row.get('waiting_since'),
        'waiting_days': row.get('waiting_days'),
        'latest_action_log_id': row['latest_action_log_id'],
        'sources': list(row['sources']),
        'person': row['person'],
        'actions': [
            {'action_log_id': a['action_log_id'],
             'request_number': a['request_number'],
             'action_type': a['action_type'],
             'status': a['status'],
             'received_time': a['received_time'],
             'source': a['source'],
             'would_succeed': a['would_succeed'],
             'reject_messages': list(a['reject_messages'])}
            for a in row['actions']
        ],
    }


#: Verdict order for the readiness board — most urgent first.
_READINESS_RANK = {'failed': 0, 'manual': 1, 'incomplete': 2, 'rechecked': 3,
                   None: 4}


def build_readiness(snapshot) -> dict:
    """The ``xras_readiness`` envelope — the sweep's per-request preflight roll-up.

    Reads the published requests-index snapshot (no network). Rows are sorted
    red -> amber -> green; an empty board is a successful, empty report.
    """
    rows = []
    for entry in (snapshot or {}).get('rows', ()) if snapshot else ():
        verdicts = [a.get('preflight') for a in entry.get('actions', ())
                    if a.get('preflight')]
        counts = {}
        for v in verdicts:
            counts[v['status']] = counts.get(v['status'], 0) + 1
        rows.append({
            'request_number': entry.get('request_number'),
            'rollup': entry.get('preflight_rollup'),
            'status': entry.get('status'),
            'opportunity_name': entry.get('opportunity_name'),
            'pi': (entry.get('pi') or {}).get('username'),
            'pending_push': entry.get('pending_push'),
            'counts': counts,
            'messages': sorted({m for v in verdicts if v['status'] == 'failed'
                                for m in v.get('messages', ())}),
        })
    rows.sort(key=lambda r: (_READINESS_RANK.get(r['rollup'], 4),
                             str(r['request_number'])))
    return {
        'kind': 'xras_readiness',
        'generated_at': (snapshot or {}).get('generated_at') if snapshot else None,
        'total': len(rows),
        'requests': rows,
    }


def build_mnemonic_report(session, snapshot) -> dict:
    """The ``xras_mnemonic_report`` envelope — orgs to link, ranked by unblock impact.

    Reads the sweep's published snapshot (no network) and resolves each failing PI's
    org against the DB, so it needs a session — unlike the pure `build_readiness`.
    """
    from sam.queries.xras_mnemonic_report import mnemonic_unblock_report
    return mnemonic_unblock_report(session, snapshot)


def build_person_report(username, person) -> dict:
    """The ``xras_person`` envelope — a direct ``/v1/people`` probe.

    *person* is ``None`` when XRAS answered and has no such username; an
    unreachable API raises before this is called, so the two stay distinct.
    """
    return {
        'kind': 'xras_person',
        'username': username,
        'found': person is not None,
        'person': person,
    }


def build_family_report(projcode, lines) -> dict:
    """The ``xras_request_family`` envelope — a projcode's request lifecycle.

    *lines* is the ``reports/request_numbers`` list; ``family`` is ``None`` when
    XRAS has no request under that projcode, keeping not-found distinct from an
    unreachable API (which raises before this is called).
    """
    from sam.queries.xras_requests import request_family

    family = request_family(lines)
    return {
        'kind': 'xras_request_family',
        'projcode': projcode,
        'found': family is not None,
        'family': family,
    }
