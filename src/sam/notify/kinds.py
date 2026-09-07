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

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 1 and § 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping

from sam.notify.base import Channel


@dataclass(frozen=True)
class NotificationFamily:
    """One addressing class: the unit ``NOTIFY_<FAMILY>_*`` and the
    ``notification_addressing`` rows are keyed on.

    Args:
        key: the env-name fragment, ``[a-z_]`` only.
        label: human text for the admin surfaces.
        about_project: whether its kinds concern one project, so the
            template editor can preview a real one.
    """

    key: str
    label: str
    about_project: bool = True


FAMILIES: Mapping[str, NotificationFamily] = {
    f.key: f for f in (
        NotificationFamily('expiration', 'Allocation expiration notices'),
        NotificationFamily('xras', 'XRAS handoff notices'),
        NotificationFamily('task', 'Scheduled task summaries', about_project=False),
    )
}

#: The facility variants a facility-aware kind can address separately; the
#: stems the template resolver tries, so ``expiration-WNA`` names both the
#: WNA letter and a WNA-only copy list.
FACILITY_VARIANTS = ('UNIV', 'WNA')


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
        family: the addressing class, a :data:`FAMILIES` key.
            ``NotifyConfig.addressing(family)`` reads
            ``NOTIFY_<FAMILY>_{CC,BCC,FROM,REPLY_TO}`` from it.
    """

    key: str
    label: str
    template_base: str
    channel: Channel = Channel.EMAIL
    default_subscribed: bool = True
    facility_aware: bool = True
    family: str = ''


def _by_key(*kinds: NotificationKind) -> Dict[str, NotificationKind]:
    return {kind.key: kind for kind in kinds}


#: Every kind the system can send. A key not in here is a programr error and
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
        family='expiration',
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
        family='xras',
    ),
    # The other three XRAS outcomes
    #
    # One kind per action type rather than one `xras_update` branching on a
    # context key. The prose genuinely differs — a supplement reports an
    # increment, an extension reports a date — and the kind is what the admin
    # facet chips and the dedup key are built from, so folding them would
    # make "we notified them about the supplement" unaskable.
    #
    # WARNING: `transfer` deliberately has no kind: it parks as manual by design and
    # never completes, so there is no outcome to report. It still appears on
    # the activity table as history, with no Notify button — see
    # XRAS_SERVICE_KINDS in sam.queries.xras_activation, which is the map that
    # leaves it out.
    #
    # `adjust` had no kind for the same reason until the Round 2 smoke: an
    # Adjustment can be a *reduction*, and "your allocation was cut" was not a
    # mail to send before deciding what it should say. It now says it — see
    # xras_adjustment below, whose whole design is that it never claims a
    # direction the payload does not support.
    NotificationKind(
        key='xras_supplement',
        family='xras',
        label='XRAS allocation supplement',
        template_base='xras_supplement',
        default_subscribed=True,
        facility_aware=False,
    ),
    NotificationKind(
        key='xras_extension',
        family='xras',
        label='XRAS allocation extension',
        template_base='xras_extension',
        default_subscribed=True,
        facility_aware=False,
    ),
    NotificationKind(
        key='xras_update',
        family='xras',
        label='XRAS allocation renewal',
        template_base='xras_update',
        default_subscribed=True,
        facility_aware=False,
    ),
    # The one kind whose message may be bad news. Its template states the
    # signed change per resource and the resulting total, and never uses a
    # word that presumes a direction — an Adjustment is the only action type
    # that can subtract.
    NotificationKind(
        key='xras_adjustment',
        family='xras',
        label='XRAS allocation adjustment',
        template_base='xras_adjustment',
        default_subscribed=True,
        facility_aware=False,
    ),
    # The only kind addressed to an OPERATOR rather than a PI, and the only
    # one about the system rather than about a project. It exists because a
    # scheduled send is otherwise invisible: a red Kubernetes Job says
    # something went wrong and nothing about what, and a green one says
    # nothing at all — including on the ~40 weeks a year that legitimately
    # send no mail, where "green and silent" is indistinguishable from a
    # query that stopped matching.
    NotificationKind(
        key='task_summary',
        label='Scheduled task run summary',
        template_base='task_summary',
        # Transactional in the same sense as the rest: the operator asked for
        # this by scheduling the task.
        default_subscribed=True,
        # Not about a project, so it has no facility to vary on.
        facility_aware=False,
        family='task',
    ),
)


def families() -> tuple[str, ...]:
    """Every addressing family, sorted."""
    return tuple(sorted(FAMILIES))


def get_family(key: str) -> NotificationFamily:
    """Look up a family, or raise with the full vocabulary in the message."""
    try:
        return FAMILIES[key]
    except KeyError:
        raise ValueError(
            f'unknown notification family {key!r}; expected one of '
            f'{", ".join(sorted(FAMILIES))}') from None


def kinds_in_family(family: str) -> List[NotificationKind]:
    """The kinds of one family, in registration order."""
    return [k for k in NOTIFICATION_KINDS.values() if k.family == family]


def addressing_scopes(family: str) -> List[str]:
    """The ``notification_addressing.scope`` vocabulary for one family.

    The family key, then each kind key, then ``{kind}-{facility}`` for every
    facility-aware kind: the stems a message is matched against.
    """
    get_family(family)
    scopes = [family]
    for kind in kinds_in_family(family):
        scopes.append(kind.key)
        if kind.facility_aware:
            scopes.extend(f'{kind.key}-{facility}' for facility in FACILITY_VARIANTS)
    # dict.fromkeys: order-preserving dedupe; the `expiration` family and
    # kind share a key.
    return list(dict.fromkeys(scopes))


def scope_family(scope: str) -> str:
    """The family an addressing scope belongs to; raises ``ValueError`` if none."""
    for family in FAMILIES:
        if scope in addressing_scopes(family):
            return family
    raise ValueError(f'unknown addressing scope {scope!r}')


def message_scopes(kind: str, facility=None) -> List[str]:
    """The scopes one message matches: family, kind, and its facility variant."""
    k = get_kind(kind)
    scopes = [k.family, k.key]
    if k.facility_aware and facility:
        scopes.append(f'{k.key}-{facility}')
    return list(dict.fromkeys(scopes))


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
