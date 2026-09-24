"""Layer-2 builders for the HPC account-request tables.

Same contract as the rest of ``tests/factories``: ``session`` first positional,
auto-build the minimum FK graph, ``flush()`` (never ``commit()``), return the
instance. Both delegate to the model's ``create()`` so the invariants it
enforces are the ones a test exercises.
"""

from datetime import date, datetime, timedelta

from sam.core.account_requests import (
    AccountRequest, AccountRequestEvent, EventEnrollment,
)

from ._seq import next_seq
from .core import make_user
from .projects import make_project


def make_account_request_event(session, *, project=None, event_code=None,
                               name=None, accounts_needed_by=None, by='benkirk',
                               extra_sponsor=None, opens_at=None, closes_at=None,
                               active=True, listed=False, invite_only=False,
                               instructions=None):
    """An event 30 days out on a fresh project unless told otherwise.

    ``next_seq('EVT-')`` yields ``EVT-00001`` (worker-tagged), which already
    fits the event-code pattern.
    """
    project = project or make_project(session)
    event = AccountRequestEvent.create(
        session,
        event_code=event_code or next_seq('EVT-'),
        name=name or f'Event {event_code or "factory"}',
        project_id=project.project_id,
        accounts_needed_by=accounts_needed_by or (date.today() + timedelta(days=30)),
        created_by=by,
        extra_sponsor_user_id=extra_sponsor.user_id if extra_sponsor else None,
        opens_at=opens_at,
        closes_at=closes_at,
        listed=listed,
        invite_only=invite_only,
        instructions=instructions,
    )
    if not active:
        event.close()
    return event


def make_account_request(session, *, email=None, first_name='Test', last_name=None,
                         purpose='standalone', by='benkirk', project=None,
                         event=None, sponsor=None, xras_username=None,
                         verified_by='benkirk', when=None, **fields):
    """A verified ``standalone`` request by default -- the shape the queue shows.

    ``purpose='enrollment'`` builds a project when none is given;
    ``purpose='submission'`` mints a placeholder username. ``verified_by=None``
    makes the row invisible to the queue, as the public form leaves it.
    ``when`` back-dates ``creation_time`` after the fact because ``create``
    always stamps now, which is useless for testing an age rule.
    """
    if purpose == 'enrollment' and project is None:
        project = event.project_id if event else None
        if project is None:
            project = make_project(session)
    if purpose == 'submission' and xras_username is None:
        xras_username = next_seq('ph-user-')
    project_id = project if isinstance(project, int) else (
        project.project_id if project is not None else None)
    row = AccountRequest.create(
        session,
        email=email or f'{next_seq("req")}@example.edu',
        first_name=first_name,
        last_name=last_name or next_seq('Person'),
        purpose=purpose,
        created_by=by,
        project_id=project_id,
        sponsor_user_id=sponsor.user_id if sponsor else None,
        event_id=fields.pop('event_id', event.account_request_event_id if event else None),
        xras_username=xras_username,
        verified_by=verified_by,
        **fields,
    )
    if when is not None:
        row.creation_time = when
        session.flush()
    return row


def make_event_enrollment(session, *, event=None, user=None, source='self',
                          by=None, when=None):
    """One enrollment; builds an event and a user when not given."""
    event = event or make_account_request_event(session)
    user = user or make_user(session)
    return EventEnrollment.upsert(session, event=event, user=user, source=source,
                                  by=by or user.username, clock=when)
