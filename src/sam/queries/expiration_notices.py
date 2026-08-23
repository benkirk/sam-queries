"""Turns expiring allocations into ``Message`` objects, and nothing else.

Two consumers build the same expiration notice from the same 4-tuple:
``sam-admin project --upcoming-expirations --notify`` and the weekly
``expiration_notices`` scheduled task. They must not disagree about the
audience, the payload or — above all — the dedup key, because a disagreement
there is silently a second copy in a PI's inbox.

So the builder lives here, once, and both call it. It **builds** rather than
queries, which makes the module name a slight misnomer; it is here anyway
because it sits beside :mod:`sam.queries.expirations`, whose exact tuple it
consumes, and :mod:`sam.queries.notifications`, which reads back what it
caused. The one place it must **not** live is inside ``sam/notify/``: that
package is transport, ledger and rendering machinery and stays domain-free.

WARNING: **Not exported from** ``sam/queries/__init__.py``. That file imports its
submodules eagerly, so listing this one would put ``sam.notify.base`` into
the import graph of every ``from sam.queries import ...`` in the tree.
Import it by full path.

See ``docs/plans/EXPIRATION_NOTICES.md`` commit 4.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Sequence, Tuple

from sam import fmt
from sam.enums import FacilityName, ResourceTypeName
from sam.notify import Message, Recipient

#: How long after the last resource expires the project's data survives.
GRACE_PERIOD_DAYS = 90


@dataclass(frozen=True)
class Milestone:
    """One rung of the notification ladder: a **band** of days-before-expiry.

    Bands, not points, because a point event only fires if a run lands exactly
    on it. Bands tile the runway, so each project-expiration falls in exactly
    one band per run — and **a band must be at least as wide as the gap
    between runs**, or expirations fall between the rungs. That is the same
    failure mode as the old 32-day window under a monthly cadence, and it is
    why a weekly schedule is what makes a 7-day rung expressible at all.

    Attributes:
        label: goes in the dedup key and the template context. Stable
            forever — changing one re-notifies everyone it applies to.
        lo_days: inclusive lower bound, in days from the run.
        hi_days: **exclusive** upper bound. See
            ``scheduling.tasks.expiration_notices.band_bounds`` for how that
            exclusivity survives a query whose date filter is ``<=``.
    """

    label: str
    lo_days: int
    hi_days: int

    def __post_init__(self):
        if self.hi_days <= self.lo_days:
            raise ValueError(
                f'milestone {self.label!r}: hi_days ({self.hi_days}) must '
                f'exceed lo_days ({self.lo_days})')
        if not self.label:
            raise ValueError('a milestone needs a label — it is in the dedup key')


#: The ladder, shipped with a single rung.
#:
#: One band spanning the whole 40-day runway, so every expiration produces one
#: notice at 33-40 days out — which is what the manual monthly run produced,
#: only on a predictable schedule.
#:
#: **The machinery ships now and the rungs later, deliberately.** The rung
#: label is in the dedup key from day one; retrofitting it would change every
#: key and force a one-time re-notify of everyone. Paying that cost is
#: avoidable only by paying it never. Enabling the ladder below is therefore a
#: one-tuple edit with no key migration:
#:
#:     MILESTONES = (Milestone('60d', 56, 63),
#:                   Milestone('30d', 28, 35),
#:                   Milestone('7d',   7, 14))
#:
#: Bands are 7 days wide there because runs are 7 days apart. Re-tile them if
#: the schedule ever changes.
MILESTONES: Tuple[Milestone, ...] = (Milestone('expiring', 0, 40),)


def dedup_key(projcode: str, latest_expiration_date: Optional[str],
              milestone_label: str, recipient: str) -> str:
    """The suppression key: one notice per project-expiration, rung and person.

    Keyed on the expiration **date**, so a renewal or an extension mints a new
    key and is never wrongly suppressed — which is why the suppression query
    needs no time window of its own.
    """
    return (f'expiration:{projcode}:{latest_expiration_date}'
            f':{milestone_label}:{recipient}')


def legacy_dedup_key(projcode: str, latest_expiration_date: Optional[str],
                     recipient: str) -> str:
    """The pre-rung-label key format, for the migration bridge.

    Every manual CLI run before this change wrote
    ``expiration:{projcode}:{date}:{recipient}``. The first scheduled run
    would not match those, so the overlap cohort — projects already notified
    whose end dates still fall in the window — would get a second notice. The
    task checks both forms and treats a hit on either as suppressing.

    **Removable after one full cycle**, by which point every live key is in
    the new format. Nothing else should grow a dependency on it.
    """
    return f'expiration:{projcode}:{latest_expiration_date}:{recipient}'


def build_expiration_messages(
        expiring_data: Sequence[Tuple], *,
        requested_by: str,
        milestone: Milestone,
        additional_recipients: Optional[str] = None) -> List[Message]:
    """One :class:`~sam.notify.base.Message` per (project, recipient).

    Args:
        expiring_data: ``(Project, Allocation, resource_name, days_remaining)``
             4-tuples, exactly as
            :func:`sam.queries.expirations.get_all_expiring_allocations`
            returns them. Multiple rows per project are expected — one per
            expiring resource — and are folded into a single notice.
        requested_by: what lands in ``notification_log.requested_by``, which
            the admin card renders as "who asked". The CLI passes the unix
            user; the task passes ``task:expiration_notices``.
        milestone: the rung this run is sending. Its label goes in the dedup
            key and the template context.
        additional_recipients: comma-separated extra addresses, from
            ``--email-list``. Added with the ``user`` role, and never
            downgrading someone already on the roster.

    Returns:
        Messages in a deterministic order — projects in the order they first
        appear in ``expiring_data`` (which the query sorts by end date then
        projcode), recipients in roster-then-additions order.
    """
    # Group by project to send one email per project.
    projects_map = defaultdict(list)
    for proj, alloc, resource_name, days_remaining in expiring_data:
        projects_map[proj.projcode].append({
            'project': proj,
            'allocation': alloc,
            'resource_name': resource_name,
            'days_remaining': days_remaining
        })

    messages = []
    for projcode, resources_data in projects_map.items():
        project = resources_data[0]['project']

        # Get usage data for all resources
        usage = project.get_detailed_allocation_usage()

        # Calculate grace expiration (90 days after latest resource expiration)
        latest_expiration = None
        for item in resources_data:
            if item['allocation'].end_date:
                if latest_expiration is None or item['allocation'].end_date > latest_expiration:
                    latest_expiration = item['allocation'].end_date

        latest_expiration_date = None
        grace_expiration_date = None
        if latest_expiration:
            latest_expiration_date = latest_expiration.strftime("%Y-%m-%d")
            grace_expiration_date = (
                latest_expiration
                + timedelta(days=GRACE_PERIOD_DAYS)).strftime("%Y-%m-%d")

        # Determine facility for template selection
        facility_name = None
        if project.allocation_type and project.allocation_type.panel and project.allocation_type.panel.facility:
            facility_name = project.allocation_type.panel.facility.facility_name

        # Build resources list for email
        resources = []
        for item in resources_data:
            resource_name = item['resource_name']
            resource_usage = usage.get(resource_name, {})

            resources.append({
                'resource_name': resource_name,
                'expiration_date': fmt.date_str(item['allocation'].end_date, null='N/A'),
                'days_remaining': item['days_remaining'],
                'allocated_amount': resource_usage.get('allocated', 0),
                'used_amount': resource_usage.get('used', 0),
                'remaining_amount': resource_usage.get('remaining', 0),
                # Was hardcoded 'core-hours', which told a PI with a DISK
                # or ARCHIVE allocation that their TiB-years were
                # core-hours. ResourceTypeName.allocation_unit is the one
                # source the dashboard and the CLI already share; it also
                # returns None for an access-boolean grant (amount == 1),
                # so the notice stops rendering "1 hours".
                'units': ResourceTypeName.allocation_unit(
                    resource_usage.get('resource_type'),
                    resource_usage.get('allocated')),
            })

        # Build recipients dict: email -> (name, role)
        # Start with roster (all users default to 'user' role)
        recipients = {}
        for user in project.roster:
            if user.primary_email:
                recipients[user.primary_email] = (user.display_name, 'user')

        # Override with admin role (higher priority than user)
        if project.admin and project.admin.primary_email:
            recipients[project.admin.primary_email] = (project.admin.display_name, 'admin')

        # Override with lead role (highest priority)
        if project.lead and project.lead.primary_email:
            recipients[project.lead.primary_email] = (project.lead.display_name, 'lead')

        # Add additional recipients if provided (default to 'user' role)
        if additional_recipients:
            for email in additional_recipients.split(','):
                email = email.strip()
                if email and email not in recipients:
                    recipients[email] = (email, 'user')

        # Lead details for the templates.
        #
        # `.primary_email` used to be read unguarded, two lines below a
        # guarded `project_lead_name`. Measured, that asymmetry is NOT the
        # crash it looks like: `project.project_lead_user_id` is NOT NULL
        # with an enforced FK (`project_lead_user_fk`, 0 dangling rows), so
        # `project.lead` cannot be None, and `primary_email` returns None
        # rather than raising when a lead has no address on file. The guard
        # is kept for consistency with the line above it, not because it
        # fixes a reachable AttributeError. What IS reachable — and what
        # the templates must cope with — is `project_lead_email is None`.
        project_lead_name = project.lead.display_name if project.lead else 'Project Lead'
        project_lead_email = project.lead.primary_email if project.lead else None

        subject = f'NSF NCAR Project {projcode} Expiration Notice'
        if facility_name == FacilityName.WNA:
            subject = f'NCAR/Wyoming Computing Project {projcode} Expiration Notice'

        # One Message per recipient — the ledger records one row per
        # person, and suppression keys on the recipient.
        for recipient_email, (recipient_name, recipient_role) in recipients.items():
            messages.append(Message(
                kind='expiration',
                recipient=Recipient(recipient_email, name=recipient_name,
                                    role=recipient_role),
                subject=subject,
                context={
                    'project_code': projcode,
                    'project_title': project.title,
                    'project_lead': project_lead_name,
                    'project_lead_email': project_lead_email,
                    'resources': resources,
                    'latest_expiration': latest_expiration_date,
                    'grace_expiration': grace_expiration_date,
                    'facility': facility_name,
                    'milestone': milestone.label,
                },
                facility=facility_name,
                entity=('project', project.project_id),
                projcode=projcode,
                dedup_key=dedup_key(projcode, latest_expiration_date,
                                    milestone.label, recipient_email),
                requested_by=requested_by,
            ))

    return messages
