"""Placeholders SAM can merge now, ranked by the pushes each merge unblocks.

A pivot over worklist rows :func:`~sam.queries.xras_accounts.stamp_merge_targets`
already stamped -- no second derivation, no network. Sibling of the mnemonic
and contract reports; like them, NOT exported from ``sam/queries/__init__.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

_SAMPLE_CAP = 10


def _item(row: Dict[str, Any], numbers: List[str]) -> Dict[str, Any]:
    target = row.get('merge_target') or {}
    person = row.get('person') or {}
    return {
        'username': row.get('username'),
        'target_username': target.get('username'),
        'target_active': bool(target.get('active')),
        'email': person.get('email'),
        'is_reconciled': row.get('is_reconciled'),
        'roles': list(row.get('roles') or ()),
        'waiting_since': row.get('waiting_since'),
        'waiting_days': row.get('waiting_days'),
        'unblock_count': len(numbers),
        'sample': numbers[:_SAMPLE_CAP],
    }


def identity_merge_report(rows: Iterable[Dict[str, Any]], *,
                          generated_at: Any = None,
                          in_view: Optional[Iterable[str]] = None) -> dict:
    """``{'kind', 'generated_at', 'targets', 'reactivations', 'needs_account'}``.

    Every request naming an absent identity is blocked by it, so a row's
    unblock count is its distinct request numbers -- restricted to *in_view*
    when the caller ranks over the rows an operator is looking at.
    """
    wanted = None if in_view is None else {str(n).strip() for n in in_view if n}
    targets: List[dict] = []
    reactivations: List[dict] = []
    needs_account: List[dict] = []
    for row in rows:
        if not row.get('placeholder'):
            continue
        numbers = list(dict.fromkeys(
            str(a.get('request_number')).strip()
            for a in (row.get('actions') or ()) if a.get('request_number')))
        if wanted is not None:
            numbers = [n for n in numbers if n in wanted]
            if not numbers:
                continue
        item = _item(row, numbers)
        if row.get('remedy') == 'merge':
            targets.append(item)
        elif row.get('merge_target'):
            reactivations.append(item)
        else:
            needs_account.append(item)
    key = lambda t: (-t['unblock_count'], t['username'] or '')     # noqa: E731
    return {
        'kind': 'xras_identity_report',
        'generated_at': generated_at,
        'targets': sorted(targets, key=key),
        'reactivations': sorted(reactivations, key=key),
        'needs_account': sorted(needs_account, key=key),
    }
