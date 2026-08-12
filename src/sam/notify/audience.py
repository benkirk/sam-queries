"""Adapters from domain objects to :class:`~sam.notify.base.Recipient`.

The two consumers keep **two audiences**, which is correct: expiration
notices go to roster + admin + lead, while the XRAS activation handoff goes
to lead + admin only (``get_xras_pending_recipients``, deliberately kept off
the ``VIEW_XRAS`` render path so contact PII never reaches a viewer who is
not entitled to it). What they share is the conversion, and that is here.

Their name formatting is now the **same**: ``display_name`` first, for both.
It used to diverge (XRAS took ``full_name or username``) and that divergence
was recorded here as deliberate, on the grounds that unifying it changed
pinned strings for no benefit. The benefit turned up in the pre-deploy smoke:
XRAS mail greeted a PI as "Benjamin Shelton Kirk" while every other surface in
the product called him "Ben Kirk". ``display_name`` is (nickname or first) +
last; XRAS keeps ``full_name`` then ``username`` behind it as the fallback
chain, since ``display_name`` drops a middle name.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 7.
"""

from __future__ import annotations

from typing import Iterable, List, Mapping

from sam.notify.base import Channel, Recipient


def to_recipients(people: Iterable[Mapping[str, str]], *,
                  default_role: str = 'user',
                  channel: Channel = Channel.EMAIL) -> List[Recipient]:
    """Convert ``[{'name', 'email', 'role'}]`` into recipients.

    This is the shape ``sam.queries.xras_activation.get_xras_pending_recipients``
    returns.

    Entries with no address are **dropped rather than raising**: a project
    whose lead has nothing on file is a real state (one such project exists
    in the snapshot), and it must cost that person their notice, not everyone
    else theirs.

    Duplicate addresses collapse to the **first** occurrence, because callers
    order their lists by descending role precedence — lead before admin —
    and one person holding two roles should get one message naming the
    higher one, not two messages.
    """
    recipients: List[Recipient] = []
    seen = set()
    for person in people:
        address = (person.get('email') or '').strip()
        if not address or address in seen:
            continue
        seen.add(address)
        recipients.append(Recipient(
            address=address,
            name=person.get('name') or None,
            role=person.get('role') or default_role,
            channel=channel,
        ))
    return recipients
