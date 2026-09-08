"""Layer-2 factory for ``notification_log`` rows.

Delegates to the model's own ``create()`` (the pattern in
``tests/factories/xras.py:103-119``), so validation the production path
performs is not bypassed by the test path.

WARNING: **Synthetic keys derive from the DB-assigned PK, never a process-local
counter.** Under xdist, workers share one database; a module-level counter
gives worker gw0 and worker gw1 the same ``dedup_key``, and a suppression
test then passes or fails depending on which worker got there first. Same
hazard the XRAS factory documents.
"""

import uuid
from datetime import datetime, timedelta

from sam import NotificationAddressing, NotificationLog, NotificationTemplateOverride


def make_notification_log(session, *, kind='expiration', channel='email',
                          transport='null', status='sent',
                          recipient=None, requested_by='benkirk',
                          intended_recipient=None, recipient_name='A PI',
                          recipient_role='lead', subject=None, template=None,
                          copies=None, entity_type=None, entity_id=None, projcode=None,
                          dedup_key=None, error=None, when=None, age=None):
    """One ledger row, optionally back-dated.

    Args:
        when: an explicit ``creation_time``.
        age: a ``timedelta`` *before now* — the readable way to write "older
            than the staleness horizon", which is the one case a clock-free
            test cannot reach.
        recipient: defaults to an address derived from the row's own PK, so
            two factory calls never collide under xdist.
        dedup_key: defaults to a key built from the assigned PK, for the same
            reason. Pass one explicitly whenever the test is *about*
            suppression.
    """
    if age is not None and when is None:
        when = datetime.now() - age

    row = NotificationLog.create(
        session,
        kind=kind,
        channel=channel,
        transport=transport,
        status=status,
        # Placeholder; rewritten below once the PK exists.
        recipient=recipient or 'placeholder@example.edu',
        requested_by=requested_by,
        intended_recipient=intended_recipient,
        recipient_name=recipient_name,
        recipient_role=recipient_role,
        subject=subject if subject is not None else f'{kind} notice',
        template=template,
        copies=copies,
        entity_type=entity_type,
        entity_id=entity_id,
        projcode=projcode,
        dedup_key=dedup_key,
        error=error,
        when=when,
    )

    pk = row.notification_log_id
    if recipient is None:
        row.recipient = f'user{pk}@example.edu'
    if dedup_key is None:
        row.dedup_key = f'{kind}:FACTORY{pk}:{pk}:{row.recipient}'
    session.flush()
    return row


def make_template_override(session, *, name, body='OVERRIDE {{ project_code }}',
                           modified_by='benkirk'):
    """One override row. Pass a name unique to the test: `name` is UNIQUE and
    xdist workers share the database."""
    return NotificationTemplateOverride.create(session, name=name, body=body,
                                               modified_by=modified_by)


def make_addressing_row(session, *, scope='xras', field='cc', address=None,
                        created_by='benkirk'):
    """One operator-added copy. `address` defaults to a per-call unique
    mailbox: (scope, field, address) is UNIQUE and xdist workers share the
    database."""
    if address is None:
        address = f'copy-{uuid.uuid4().hex[:10]}@example.edu'
    return NotificationAddressing.create(session, scope=scope, field=field,
                                         address=address, created_by=created_by)
