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
    XRAS_ACTION_STATUSES,
    get_recent_xras_actions,
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

    # The replay lineage, both directions: what this replayed, and what replayed it.
    children = get_recent_xras_actions(session, replay_of=action_id,
                                       sort_by='received_time', sort_dir='asc')

    return {
        'kind':           'xras_action',
        'action_log_id':  action_id,
        'action':         action,
        'replay_of_id':   action['replay_of_id'],
        'replays':        [{'action_log_id': c['action_log_id'],
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
        # Every status appears, including at zero — an absent bucket reads as
        # "not measured" rather than "none". Same rule as the contract audit's
        # always-present check sections.
        'by_status':  {s: summary['by_status'].get(s, 0)
                       for s in XRAS_ACTION_STATUSES},
        'by_type':    summary['by_type'],
    }


def build_replay_result(action_id: int, new_id: int, *, actor: str,
                        action: Dict[str, Any]) -> Dict[str, Any]:
    """The ``xras_replay`` envelope. Only ever reached via ``--json-writes``."""
    return {
        'kind':           'xras_replay',
        'replayed_id':    action_id,
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
