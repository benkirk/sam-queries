"""``NOTIFICATION_KINDS`` — the vocabulary, and the one policy decision in it.

A *kind* is what a notification is about. It names the template base, the
channel, and whether an absent preference means "send" or "stay quiet".

**Why ``default_subscribed`` lives here and not in a table.** A subscription
table was designed and cut (§ 6): shipping one dormant, with no consumer to
validate its shape, costs the same DBA ticket to *alter* that it was meant to
save. But one idea from it survives, because it is a Python decision available
now — **"no row" must not be the policy; the kind must be.** Expiration
notices are transactional: a PI has to be told, and a missing preference row
must never silence that. Operational feeds are opt-in. Any future table then
expresses only *deviation* from the kind's default, which is what keeps it
small.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 1 and § 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from sam.notify.base import Channel


@dataclass(frozen=True)
class NotificationKind:
    """One notification kind.

    Args:
        key: the stable identifier, stored in ``notification_log.kind`` and
            used as the ``dedup_key`` prefix. Keep it ``VARCHAR(32)``-safe.
        label: human text for the admin surfaces.
        template_base: resolves to ``{base}-{facility}.{txt,html}``, falling
            back to ``{base}-UNIV`` — see :mod:`sam.notify.render`.
        channel: which transport family delivers it.
        default_subscribed: what an absent preference means. See above.
        facility_aware: whether the facility variants are meaningful. A kind
            that is not facility-aware always renders the default variant.
    """

    key: str
    label: str
    template_base: str
    channel: Channel = Channel.EMAIL
    default_subscribed: bool = True
    facility_aware: bool = True


def _by_key(*kinds: NotificationKind) -> Dict[str, NotificationKind]:
    return {kind.key: kind for kind in kinds}


#: Every kind the system can send. A key not in here is a programmer error and
#: :class:`~sam.notify.service.Notifier` raises on it — the column is a bare
#: ``VARCHAR``, so this dict is the only thing between a typo and a ledger row
#: that no facet chip will ever match.
NOTIFICATION_KINDS: Mapping[str, NotificationKind] = _by_key(
    NotificationKind(
        key='expiration',
        label='Allocation expiration notice',
        template_base='expiration',
        # Transactional. A PI whose allocation lapses must be told, and an
        # absent preference row must never be the reason they were not.
        default_subscribed=True,
    ),
    NotificationKind(
        key='xras_activation',
        label='XRAS project activation',
        template_base='xras_activation',
        # Also transactional: this is the mail legacy SAM sent on activation
        # and that nobody sends after cutover unless SAM does.
        default_subscribed=True,
        # The handoff text is the same whoever the facility is; there is one
        # variant and the resolver finds it through the default.
        facility_aware=False,
    ),
)


def get_kind(key: str) -> NotificationKind:
    """Look up a kind, or raise with the full vocabulary in the message.

    Raises:
        ValueError: on an unknown key.
    """
    try:
        return NOTIFICATION_KINDS[key]
    except KeyError:
        raise ValueError(
            f'unknown notification kind {key!r}; expected one of '
            f'{", ".join(sorted(NOTIFICATION_KINDS))}') from None
