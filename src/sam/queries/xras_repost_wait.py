"""How long a refused XRAS post waits for the re-post that applies it.

An episode is one request line's refused posts (``is_post``, ``failed``, 422) up to the
first ``processed`` post linked to it: the same ``action_id``, else the same ``requestId``
and action type. Phases: (a) first refusal -> blocker cleared, (b) cleared -> applied,
(c) the total. A blocker's clearing is the earliest SAM-side signal in the episode: a
verified ``merge_person``, ``account_request.merged_at``, ``users.creation_time`` or
``contract.creation_time``. An episode with any blocker SAM records no clearing for
(mnemonic, inactive person, other) has no ``cleared_at`` and counts in (c) only.
Read-only. Runner: scripts/xras_repost_wait.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog

#: ``message_kind`` of a blocker line, plus ``other`` for a line it does not classify.
BLOCKER_KINDS = ('placeholder', 'person', 'contract', 'mnemonic', 'other')


@dataclass
class Blocker:
    kind: str
    subject: str = ''
    cleared_at: Optional[datetime] = None
    source: Optional[str] = None
    core: str = ''


@dataclass
class WaitEpisode:
    request_id: Optional[int]
    action_type: str
    request_number: str
    action_ids: Tuple[int, ...]
    first_refused: datetime
    last_refused: datetime
    refusals: int
    blockers: List[Blocker] = field(default_factory=list)
    applied_at: Optional[datetime] = None
    applied_log_id: Optional[int] = None
    linked_by: Optional[str] = None
    projcode: Optional[str] = None
    ticket_key: Optional[str] = None
    ticket_opened: Optional[datetime] = None
    repost_asked: Optional[datetime] = None

    @property
    def cleared_at(self) -> Optional[datetime]:
        """The last blocker's clearing, or ``None`` when any blocker has none."""
        if not self.blockers or any(b.cleared_at is None for b in self.blockers):
            return None
        return max(b.cleared_at for b in self.blockers)

    @property
    def kinds(self) -> List[str]:
        return sorted({b.kind for b in self.blockers}, key=BLOCKER_KINDS.index)

    def phases(self, until: datetime) -> Dict[str, Optional[timedelta]]:
        cleared, applied = self.cleared_at, self.applied_at
        return {
            'a': cleared - self.first_refused if cleared else None,
            'b': applied - cleared if applied and cleared else None,
            'c': applied - self.first_refused if applied else None,
            'age': None if applied else until - self.first_refused,
            'since_cleared': until - cleared if cleared and not applied else None,
        }

    def as_dict(self, until: datetime) -> Dict[str, Any]:
        def iso(v):
            return v.isoformat(sep=' ', timespec='minutes') if v else None
        hours = {k: _hours(v) for k, v in self.phases(until).items()}
        return {
            'request_id': self.request_id, 'request_number': self.request_number,
            'action_type': self.action_type, 'action_ids': list(self.action_ids),
            'refusals': self.refusals, 'first_refused': iso(self.first_refused),
            'last_refused': iso(self.last_refused), 'cleared_at': iso(self.cleared_at),
            'applied_at': iso(self.applied_at), 'applied_log_id': self.applied_log_id,
            'linked_by': self.linked_by, 'projcode': self.projcode,
            'ticket_key': self.ticket_key, 'ticket_opened': iso(self.ticket_opened),
            'repost_asked': iso(self.repost_asked),
            'blockers': [{'kind': b.kind, 'subject': b.subject,
                          'cleared_at': iso(b.cleared_at), 'source': b.source}
                         for b in self.blockers],
            'hours': hours,
        }


def _hours(delta: Optional[timedelta]) -> Optional[float]:
    return None if delta is None else round(delta.total_seconds() / 3600, 2)


def _type(row) -> str:
    from sam.queries.xras_actions import canonical_action_type
    return canonical_action_type(row.action_type) or ''


def _line_key(row) -> Tuple:
    # requestId survives the New's rename; actionId alone would split a withdrawn and
    # resubmitted action into two episodes.
    if row.request_id is not None:
        return ('request', row.request_id, _type(row))
    if row.action_id is not None:
        return ('action', row.action_id)
    return ('number', row.request_number or '', _type(row))


def _blockers(messages: Iterable[str]) -> List[Blocker]:
    from sam.queries.xras_request_tickets import CONTRACT_BLOCKER
    from sam.queries.xras_requests import message_kind, person_username
    seen: Dict[Tuple[str, str], Blocker] = {}
    for m in messages:
        kind, core = message_kind(m) or 'other', ''
        if kind in ('placeholder', 'person'):
            subject = (person_username(m) or '').lower()
        elif kind == 'contract':
            found = CONTRACT_BLOCKER.match(m)
            subject, core = found.group(2, 3) if found else ('', '')
        else:
            subject = m
        seen.setdefault((kind, subject), Blocker(kind, subject, core=core))
    return list(seen.values())


def repost_wait_episodes(session: Session, *, since: datetime,
                         until: Optional[datetime] = None) -> List[WaitEpisode]:
    """Every episode whose first refusal falls in ``[since, until]``, oldest first."""
    from sam.queries.xras_actions import split_error_messages
    from sam.queries.xras_request_tickets import refused_posts
    until = until or datetime.now()
    # No lower bound: an episode begun before *since* must keep its real first refusal,
    # or a later in-window refusal would pose as the start and shorten the wait.
    refused = (session.query(XrasActionLog)
               .filter(*refused_posts(), XrasActionLog.received_time <= until)
               .order_by(XrasActionLog.received_time, XrasActionLog.xras_action_log_id)
               .all())
    if not refused:
        return []
    action_ids = sorted({r.action_id for r in refused if r.action_id is not None})
    request_ids = sorted({r.request_id for r in refused if r.request_id is not None})
    links = [XrasActionLog.action_id.in_(action_ids)] if action_ids else []
    if request_ids:
        links.append(XrasActionLog.request_id.in_(request_ids))
    applied = (session.query(XrasActionLog)
               .filter(XrasActionLog.is_post, XrasActionLog.status == 'processed',
                       XrasActionLog.received_time <= until, or_(*links))
               .order_by(XrasActionLog.received_time, XrasActionLog.xras_action_log_id)
               .all()) if links else []

    by_line: Dict[Tuple, List[XrasActionLog]] = {}
    for row in refused:
        by_line.setdefault(_line_key(row), []).append(row)

    episodes: List[WaitEpisode] = []
    for rows in by_line.values():
        while rows:
            first = rows[0]
            ids = {r.action_id for r in rows if r.action_id is not None}
            hit = next((p for p in applied if p.received_time > first.received_time and (
                (p.action_id is not None and p.action_id in ids) or
                (first.request_id is not None and p.request_id == first.request_id
                 and _type(p) == _type(first)))), None)
            mine = [r for r in rows if hit is None or r.received_time < hit.received_time]
            rows = rows[len(mine):]
            messages = [m for r in mine for m in split_error_messages(r.error_messages)]
            episodes.append(WaitEpisode(
                request_id=first.request_id, action_type=_type(first),
                request_number=mine[-1].request_number or '',
                action_ids=tuple(sorted({r.action_id for r in mine if r.action_id is not None})),
                first_refused=first.received_time, last_refused=mine[-1].received_time,
                refusals=len(mine), blockers=_blockers(messages),
                applied_at=hit.received_time if hit else None,
                applied_log_id=hit.xras_action_log_id if hit else None,
                linked_by=None if hit is None else (
                    'action_id' if hit.action_id in ids else 'request_id'),
                projcode=hit.projcode_result if hit else None))
    episodes = [e for e in episodes if e.first_refused >= since]
    _clearings(session, episodes, until)
    _tickets(session, episodes)
    return sorted(episodes, key=lambda e: (e.first_refused, e.request_id or 0))


def _stamp(episode: WaitEpisode, blocker: Blocker, when: Optional[datetime], source: str,
           until: datetime) -> None:
    # Only a signal inside the episode can have cleared it.
    end = episode.applied_at or until
    if when is None or not (episode.first_refused <= when <= end):
        return
    if blocker.cleared_at is None or when < blocker.cleared_at:
        blocker.cleared_at, blocker.source = when, source


def _clearings(session: Session, episodes: Sequence[WaitEpisode], until: datetime) -> None:
    from sam.core.account_requests import AccountRequest
    from sam.core.users import User
    from sam.integration.xras import XrasRemediationEvent
    from sam.projects.contracts import Contract
    from sam.xras.extractors import contract_candidates

    people = {b.subject for e in episodes for b in e.blockers
              if b.kind in ('placeholder', 'person') and b.subject}
    merges: Dict[str, List[datetime]] = {}
    requests: Dict[str, List[datetime]] = {}
    created: Dict[str, datetime] = {}
    if people:
        names = sorted(people)
        for username, when in (session.query(
                XrasRemediationEvent.username,
                func.coalesce(XrasRemediationEvent.completed_time,
                              XrasRemediationEvent.creation_time))
                .filter(XrasRemediationEvent.operation == 'merge_person',
                        XrasRemediationEvent.status == 'verified',
                        func.lower(XrasRemediationEvent.username).in_(names))):
            merges.setdefault(username.lower(), []).append(when)
        for username, when in (session.query(AccountRequest.xras_username,
                                             AccountRequest.merged_at)
                               .filter(AccountRequest.xras_username.in_(names),
                                       AccountRequest.merged_at.is_not(None))):
            requests.setdefault(username.lower(), []).append(when)
        for username, when in (session.query(User.username, User.creation_time)
                               .filter(User.username.in_(names))):
            created[username.lower()] = when

    contracts: Dict[str, Optional[datetime]] = {}
    for e in episodes:
        for b in e.blockers:
            if b.kind in ('placeholder', 'person'):
                for when in merges.get(b.subject, ()):
                    _stamp(e, b, when, 'merge_person', until)
                for when in requests.get(b.subject, ()):
                    _stamp(e, b, when, 'account_request.merged_at', until)
                if b.kind == 'person':
                    _stamp(e, b, created.get(b.subject), 'users.creation_time', until)
            elif b.kind == 'contract' and b.subject:
                if b.subject not in contracts:
                    row = Contract.get_by_number(session, b.subject)
                    if row is None and b.core:
                        found = contract_candidates(session, b.core)
                        row = found[0] if len(found) == 1 else None
                    contracts[b.subject] = row.creation_time if row is not None else None
                _stamp(e, b, contracts[b.subject], 'contract.creation_time', until)


def _tickets(session: Session, episodes: Sequence[WaitEpisode]) -> None:
    """The NUSD ticket and its first "repost it" comment, from SAM's own rows."""
    from sam.integration.tickets.models import ExternalTicket
    from sam.notify.models import NotificationLog
    from sam.queries.xras_request_tickets import ENTITY_TYPE, LEDGER_KIND
    rids = sorted({e.request_id for e in episodes if e.request_id is not None})
    if not rids:
        return
    tickets = (session.query(ExternalTicket.entity_id, ExternalTicket.ticket_key,
                             ExternalTicket.creation_time)
               .filter(ExternalTicket.entity_type == ENTITY_TYPE,
                       ExternalTicket.entity_id.in_(rids))
               .order_by(ExternalTicket.creation_time).all())
    asks = (session.query(NotificationLog.entity_id, NotificationLog.creation_time)
            .filter(NotificationLog.kind == LEDGER_KIND,
                    NotificationLog.entity_type == ENTITY_TYPE,
                    NotificationLog.entity_id.in_(rids),
                    NotificationLog.status == 'sent',
                    or_(NotificationLog.dedup_key.like('%:merged:%'),
                        NotificationLog.dedup_key.like('%:contract:%')))
            .order_by(NotificationLog.creation_time).all())
    for e in episodes:
        end = e.applied_at or datetime.max
        t = next((t for t in tickets if t.entity_id == e.request_id
                  and e.first_refused <= t.creation_time <= end), None)
        if t is not None:
            e.ticket_key, e.ticket_opened = t.ticket_key, t.creation_time
        e.repost_asked = next((a.creation_time for a in asks if a.entity_id == e.request_id
                               and e.first_refused <= a.creation_time <= end), None)


def wait_stats(deltas: Iterable[Optional[timedelta]]) -> Dict[str, Any]:
    """``{n, median_h, p90_h, max_h}`` in hours; p90 is nearest-rank."""
    hours = sorted(d.total_seconds() / 3600 for d in deltas if d is not None)
    if not hours:
        return {'n': 0, 'median_h': None, 'p90_h': None, 'max_h': None}
    mid = len(hours) // 2
    median = hours[mid] if len(hours) % 2 else (hours[mid - 1] + hours[mid]) / 2
    p90 = hours[max(0, math.ceil(0.9 * len(hours)) - 1)]
    return {'n': len(hours), 'median_h': round(median, 2), 'p90_h': round(p90, 2),
            'max_h': round(hours[-1], 2)}


def _group(episodes: Sequence[WaitEpisode], until: datetime) -> Dict[str, Any]:
    phases = [e.phases(until) for e in episodes]
    waiting = [p for e, p in zip(episodes, phases) if e.applied_at is None]
    return {
        'episodes': len(episodes),
        'applied': len(episodes) - len(waiting),
        'a': wait_stats(p['a'] for p in phases),
        'b': wait_stats(p['b'] for p in phases),
        'c': wait_stats(p['c'] for p in phases),
        'waiting': len(waiting),
        'waiting_age': wait_stats(p['age'] for p in waiting),
    }


def repost_wait_report(session: Session, *, since: datetime,
                       until: Optional[datetime] = None,
                       include_episodes: bool = False) -> Dict[str, Any]:
    """The rollup: overall, per action type, per blocker kind (an episode counts under
    each kind it had), and the still-waiting set split by whether SAM saw it cleared."""
    until = until or datetime.now()
    episodes = repost_wait_episodes(session, since=since, until=until)
    by_type: Dict[str, List[WaitEpisode]] = {}
    by_kind: Dict[str, List[WaitEpisode]] = {}
    for e in episodes:
        by_type.setdefault(e.action_type or '?', []).append(e)
        for kind in e.kinds or ['other']:
            by_kind.setdefault(kind, []).append(e)
    waiting = [e for e in episodes if e.applied_at is None]
    cleared_waiting = [e for e in waiting if e.cleared_at is not None]
    report = {
        'since': since.isoformat(sep=' ', timespec='minutes'),
        'until': until.isoformat(sep=' ', timespec='minutes'),
        'overall': _group(episodes, until),
        'by_action_type': {k: _group(v, until) for k, v in sorted(by_type.items())},
        'by_blocker': {k: _group(by_kind[k], until) for k in BLOCKER_KINDS if k in by_kind},
        'linked_by': {how: sum(1 for e in episodes if e.linked_by == how)
                      for how in ('action_id', 'request_id')},
        'ticket': {
            'opened': sum(1 for e in episodes if e.ticket_key),
            'repost_asked': sum(1 for e in episodes if e.repost_asked),
            'asked_to_applied': wait_stats(e.applied_at - e.repost_asked for e in episodes
                                           if e.applied_at and e.repost_asked),
        },
        'waiting': {
            'count': len(waiting),
            'age': wait_stats(until - e.first_refused for e in waiting),
            'cleared_not_reposted': len(cleared_waiting),
            'since_cleared': wait_stats(until - e.cleared_at for e in cleared_waiting),
            'oldest': [e.as_dict(until) for e in waiting[:10]],
        },
    }
    if include_episodes:
        report['episodes'] = [e.as_dict(until) for e in episodes]
    return report
