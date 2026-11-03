"""Plain-dict payloads for ``sam-admin masks`` (JSON as-is, rich through display.py)."""

from datetime import datetime
from typing import Iterable, Optional, Sequence

from sam.core.groups import UnixGroupMask
from sam.queries.group_masks import filter_mask_views, mask_views, sort_mask_views


def _row(view: dict) -> dict:
    m: UnixGroupMask = view['mask']
    ticket = view['ticket']
    return {
        'mask_id': m.mask_id, 'gid': m.unix_gid, 'kind': m.kind, 'group_name': view['group_name'],
        'username': m.username, 'op': m.op, 'state': view['state'], 'open': view['open'],
        'flags': list(view['flags']), 'batch_id': m.head_id,
        'ticket': {k: ticket[k] for k in ('key', 'url', 'status', 'closed_at')} if ticket else None,
        'requested_by': m.requested_by, 'note': m.note, 'creation_time': m.creation_time,
        'expires_at': m.expires_at, 'closed_at': m.closed_at, 'resolution': m.resolution,
        'closed_by': m.closed_by,
    }


def build_listing(session, *, open_only: bool, kind: Optional[str], states: Iterable[str],
                  flags: Iterable[str], q: Optional[str], now: Optional[datetime] = None) -> dict:
    rows = mask_views(session, now=now or datetime.now(), open_only=open_only)
    rows = sort_mask_views(filter_mask_views(rows, kind=kind, states=states, flags=flags, q=q),
                           'created', 'desc')
    return {'kind': 'group_masks', 'open_only': open_only, 'count': len(rows),
            'masks': [_row(r) for r in rows]}


def build_opened(masks: Sequence[UnixGroupMask], target, *, dry_run: bool, checked: Sequence[str],
                 ticket: Optional[dict]) -> dict:
    return {
        'kind': 'group_masks_open', 'dry_run': dry_run,
        'target': {'kind': target.kind, 'gid': target.gid, 'name': target.name},
        'op': masks[0].op if masks else None, 'checked': list(checked),
        'masks': [{'mask_id': m.mask_id, 'username': m.username, 'batch_id': m.head_id,
                   'expires_at': m.expires_at} for m in masks],
        'ticket': ticket,
    }


def build_withdrawn(mask: UnixGroupMask, *, dry_run: bool, commented: bool) -> dict:
    return {'kind': 'group_masks_withdraw', 'dry_run': dry_run, 'mask_id': mask.mask_id,
            'gid': mask.unix_gid, 'username': mask.username, 'op': mask.op,
            'resolution': mask.resolution, 'commented': commented}
