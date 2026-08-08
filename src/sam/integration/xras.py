#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class XrasResourceRepositoryKeyResource(Base):
    """
    Maps XRAS resource repository keys to local resources.

    This is an actual database TABLE (not a view).
    For XRAS views, see xras_views.py

    Note: This is a simple mapping table with just two columns:
    - resource_repository_key: The XRAS repository key (primary key)
    - resource_id: The local SAM resource ID (unique)
    """
    __tablename__ = 'xras_resource_repository_key_resource'

    __table_args__ = (
        Index('xras_resource_repo_key_resource_resource_rid_uniq',
              'resource_id', unique=True),
        Index('xras_resource_repo_key_resource_resource_repo_key_uniq',
              'resource_repository_key', unique=True),
    )

    resource_repository_key = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)

    resource = relationship('Resource', back_populates='xras_resource_keys')

    def __str__(self):
        return f"XRAS Key {self.resource_repository_key} -> Resource {self.resource_id}"

    def __repr__(self):
        return f"<XrasResourceRepositoryKeyResource(key={self.resource_repository_key}, resource_id={self.resource_id})>"


#----------------------------------------------------------------------------
class XrasActionLog(Base):
    """Audit trail for ``POST /api/xras/v1/actions`` — one row per post.

    This is an actual database TABLE (not a view).

    Legacy SAM's only record of an XRAS action is an email to ``hdt@ucar.edu``
    (``EmailingActionPostService``), and its only replay mechanism is pasting the
    JSON back into a PrimeFaces form. ``actionJson`` is never logged at any level.
    This table replaces both: the row is written **before** dispatch, so an action
    that explodes in a handler is still recorded and replayable.

    Ordering matters and is the whole point — a row written only on success is a
    success log, not an audit trail. The row must also survive a handler rollback:
    ``management_transaction`` rolls the whole session back on exception, so this
    row is committed outside it.

    ``request_number`` vs ``projcode_result``: XRAS sends ``requestNumber`` as the
    **projcode** for actions against an existing project (Extension, Supplement,
    Update) and as a request token for New (``NCAR####`` here — the family is
    named by ``sam.queries.xras_actions.XRAS_REQUEST_TOKEN_PREFIXES``). The two
    columns therefore diverge exactly on the New path, where a projcode is
    minted — which is what makes both worth storing.

    ``raw_payload`` is ``Text`` rather than MySQL ``JSON``, and **not** merely
    because the rest of this old schema is. Two measured reasons, either of which
    is sufficient (verified against MySQL 9.7; MySQL 8 behaves the same):

    1. **A ``JSON`` column cannot store a malformed body.**
       ``INSERT ... VALUES ('{"actionType": ')`` fails with ``ERROR 3140 Invalid
       JSON text``. Auditing unparseable payloads is the whole point of the 400
       path — legacy's failure mode is a 500 with an opaque timestamp and no
       record of what arrived — so the one row we most need to write is the one
       ``JSON`` refuses.
    2. **``JSON`` is not byte-preserving, and this column is defined as the body
       verbatim, before parsing.** MySQL parses to a normalised binary form and
       re-serialises on read: it re-sorts keys by length-then-bytewise, inserts
       whitespace, and *silently collapses duplicate keys*. Round-tripping a real
       payload reordered all 23 top-level keys and grew it 2,213 → 2,375 bytes.
       That destroys the audit record's fidelity, its value as a replay source,
       and its value as a harvested fixture — the key order on the wire is what
       reveals Jackson's ``@JsonPropertyOrder``.

    Nothing is given up: deep ad-hoc querying still works over ``Text`` via
    ``JSON_EXTRACT(raw_payload, '$.roles[0].roleType')`` guarded by
    ``JSON_VALID(raw_payload)``, and every field worth filtering on
    (``action_type``, ``request_number``, ``status``, ``projcode_result``) is
    already a real, indexed column. The only thing a ``JSON`` column would add is
    a *functional index* on a nested path; if that is ever needed, add a nullable
    ``payload_json JSON`` alongside — populated only when parsing succeeded —
    which is additive and backfillable rather than a migration.

    Observed real bodies are 2.8–7.3 KB, so ``Text``'s 64 KB is ample headroom.
    """
    __tablename__ = 'xras_action_log'

    __table_args__ = (
        Index('xras_action_log_received', 'received_time'),
        Index('xras_action_log_status', 'status'),
        # The triage axis — "failed New actions" is the 55% failure cohort and the
        # table's default filter. The status-only index above is kept for the
        # status-only rollups (the page's summary strip, ``sam-admin xras --summary``).
        Index('xras_action_log_triage', 'status', 'action_type'),
        Index('xras_action_log_request', 'request_number'),
        Index('xras_action_log_action', 'action_id'),
        Index('xras_action_log_replay_fk', 'replay_of_id'),
    )

    xras_action_log_id = Column(Integer, primary_key=True, autoincrement=True)

    #: Always stamped from the *app* clock, never a DB default — see the note in
    #: ``webapp/api/xras/actions.py::_record``. The DDL deliberately carries no
    #: ``DEFAULT CURRENT_TIMESTAMP``: it resolves in the MySQL server's timezone
    #: (UTC in the containers) while SAM's convention is naive-Mountain, which put
    #: ``received_time`` six hours ahead of ``processed_time``.
    received_time = Column(DateTime, nullable=False)

    #: ``api_credentials.username`` of the caller — 'XRAS' in production. A replay
    #: row inherits the *original's* actor, because the bytes still originated at
    #: XRAS; the human who clicked Replay is recorded in ``processed_by``.
    remote_actor = Column(String(11), nullable=False)

    #: NULL when the body could not be parsed, in which case we do not know it.
    action_type = Column(String(32))
    request_number = Column(String(30))

    #: The wire's ``actionId`` — the only identifier for the *action*, and therefore
    #: the idempotency key. ``requestId`` is deliberately not stored:
    #: ``request_number`` already addresses the request in the form operators use.
    #:
    #: XRAS owns the retry, so this is about **detection**, not prevention. Three
    #: identical posts otherwise produce three rows identical in every filterable
    #: column, and the cost of not noticing is asymmetric — Extension writes nothing
    #: on a repeat, Supplement adds a full increment.
    action_id = Column(Integer)

    #: Which legacy service handled it — one of :data:`sam.xras.dispatch.SERVICES`.
    #: Recorded on the ``manual`` arm too, which is the whole point: four parking
    #: causes are otherwise byte-identical.
    service = Column(String(16))

    #: Why it parked or failed, in words. Deliberately **not** ``error_messages``,
    #: which means "the 422 body XRAS received" and is a wire contract.
    outcome_reason = Column(String(255))

    raw_payload = Column(Text, nullable=False)

    #: received | processed | manual | failed | replayed
    status = Column(String(16), nullable=False)

    #: The HTTP code we answered: 200, 400 or 422. ``status='failed'`` covers a
    #: malformed body (400), a schema rejection (422), a handler rejection (422) and
    #: an oversized body (422) — an operator triaging the log needs to tell those
    #: apart, and it stops being derivable from ``status``.
    #:
    #: ``SmallInteger`` to match the DDL's ``SMALLINT UNSIGNED``. It was ``Integer``,
    #: which is harmless in MySQL but is the kind of drift that makes a guard computed
    #: from the ORM quietly wrong — pinned by ``tests/stress/test_audit_row_survives.py``.
    http_status = Column(SmallInteger)

    #: The ordered error list, one message per line — the same list the 422 carries.
    error_messages = Column(Text)

    projcode_result = Column(String(30))
    processed_time = Column(DateTime)
    processed_by = Column(String(35))

    replay_of_id = Column(Integer, ForeignKey('xras_action_log.xras_action_log_id'))

    replay_of = relationship('XrasActionLog', remote_side=[xras_action_log_id],
                             back_populates='replays')
    replays = relationship('XrasActionLog', back_populates='replay_of')

    def __str__(self):
        return f"{self.action_type or '<unparsed>'} {self.request_number or ''} ({self.status})"

    def __repr__(self):
        return (f"<XrasActionLog(id={self.xras_action_log_id}, "
                f"action_type={self.action_type!r}, "
                f"request_number={self.request_number!r}, status={self.status!r})>")


#: The write vocabulary for ``xras_activation_event.event_type``, and the ONLY
#: enforcement point in the system — the DDL declares a bare ``VARCHAR(16)`` with
#: no ENUM and no CHECK, deliberately (an ENUM change is a DBA ticket; a string is
#: not). Validated in :meth:`XrasActivationEvent.create`.
#:
#: Kept here on the model module rather than beside ``XRAS_ACTION_STATUSES`` in
#: ``sam.queries.xras_actions``: that one is a UI *filter* vocabulary, this one is
#: the *write* vocabulary. A typo'd event type would otherwise never match the
#: derive rule and simply vanish, which is the failure mode worth making loud.
XRAS_ACTIVATION_EVENT_TYPES = (
    'notified',    # an operator asserted they handed the project off
    'dismissed',   # should not be activated via XRAS; hides the row
    'activated',   # the project was activated from the card
    'comment',     # a free note on the worklist row
    'restored',    # supersedes a dismissal — undo, appended rather than deleted
)


#----------------------------------------------------------------------------
class XrasActivationEvent(Base, SessionMixin):
    """One operator action on the XRAS pending-activation card.

    This is an actual database TABLE (not a view).

    XRAS projects arrive ``active = 0`` by design and a human activates them. The
    card that lists them stands in for the success email legacy sends and SAM has
    no mailer for; this table is the state behind its Notify / Activate / Dismiss
    / Comment actions.

    **Append-only: state is DERIVED, never stored.** There is no ``notified``
    boolean and no ``UNIQUE(project_id)``. Current state is a timestamp
    comparison against the most recent XRAS action naming the project::

        hidden from the card  iff  latest('dismissed')
                                       > MAX(latest_action, latest('restored'))
        "marked notified"     iff  latest('notified')  > latest_action

    That single rule is both the anti-spam mechanism and the re-open mechanism,
    with no episode table and no scheduled cleanup: a dismissed project reappears
    when a new Extension arrives (new information — look again), while a notified
    one stays quiet until something actually changes. A boolean gets both wrong,
    and "notified 3 times, last by benkirk" comes free. See
    ``docs/plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md`` § *The rule that does the real work*.

    **Why a new table rather than columns on ``xras_action_log``.** The card is
    keyed on *project*, and several actions can name the same project — the
    pending query already dedupes to the most recent. Notify state parked on
    "whichever action was latest when the operator clicked" disappears the moment
    a new action arrives, and the card re-notifies. That is precisely the spam
    this design prevents.

    What it *does* copy from ``XrasActionLog``: the rule that an operator action
    is recorded as a **new row, never an edit of an existing one** (see
    ``webapp/api/xras/replay.py`` §2), and ``created_by`` at ``varchar(35)``,
    ``users.username`` width, meaning "the human who clicked".

    ``xras_action_log_id`` is **provenance only** — which action prompted this.
    ``project_id`` is the key, because the card is project-scoped and therefore
    survives the action log's blind spots.
    """
    __tablename__ = 'xras_activation_event'

    __table_args__ = (
        # Serves every "latest event for this project" read, which is every read
        # the derive rule makes.
        Index('xras_activation_event_project', 'project_id', 'creation_time'),
        Index('xras_activation_event_type', 'event_type', 'creation_time'),
        Index('xras_activation_event_action_fk', 'xras_action_log_id'),
    )

    xras_activation_event_id = Column(Integer, primary_key=True, autoincrement=True)

    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)

    #: One of :data:`XRAS_ACTIVATION_EVENT_TYPES`.
    event_type = Column(String(16), nullable=False)

    #: Required for 'comment' and 'dismissed'; unused by the one-click actions.
    comment = Column(Text)

    #: Who was actually told. Recorded rather than derived because the project
    #: lead can change: "the current lead" and "who we notified" are different
    #: questions, and only the second one is an audit answer.
    notified_to = Column(Text)

    #: Provenance only — see the class docstring.
    xras_action_log_id = Column(
        Integer, ForeignKey('xras_action_log.xras_action_log_id'))

    created_by = Column(String(35), nullable=False)

    #: Stamped from the *app* clock, never a DB default. ``TimestampMixin`` is
    #: deliberately not used: its ``server_default=CURRENT_TIMESTAMP`` resolves in
    #: the MySQL server's timezone (UTC in the containers) while SAM's convention
    #: is naive-Mountain, and MySQL rounds fractional seconds rather than
    #: truncating. ``XrasActionLog`` makes the same choice for the same reason.
    creation_time = Column(DateTime, nullable=False)

    project = relationship('Project')
    xras_action = relationship('XrasActionLog')

    @classmethod
    def create(cls, session, *, project_id, event_type, created_by,
               comment=None, notified_to=None, xras_action_log_id=None):
        """Append one operator event. There is no ``update()`` — this log is
        append-only, and an undo is a superseding ``restored`` row.

        Args:
            session:            the session to add to.
            project_id:         FK to the project the operator acted on.
            event_type:         one of :data:`XRAS_ACTIVATION_EVENT_TYPES`.
            created_by:         ``users.username`` of the human who clicked.
            comment:            required for 'comment' / 'dismissed'.
            notified_to:        the recipients the operator was handed, as text.
            xras_action_log_id: provenance — the action that prompted this.

        Raises:
            ValueError: on an unknown ``event_type``. The column is a bare
                ``VARCHAR`` by design, so this is the only thing standing between
                a typo and an event that silently never matches the derive rule.
        """
        if event_type not in XRAS_ACTIVATION_EVENT_TYPES:
            raise ValueError(
                f"unknown xras_activation_event.event_type {event_type!r}; "
                f"expected one of {', '.join(XRAS_ACTIVATION_EVENT_TYPES)}")

        event = cls(
            project_id=project_id,
            event_type=event_type,
            comment=comment,
            notified_to=notified_to,
            xras_action_log_id=xras_action_log_id,
            created_by=created_by[:35],
            creation_time=datetime.now(),
        )
        session.add(event)
        session.flush()
        return event

    def __str__(self):
        return f"{self.event_type} on project {self.project_id} by {self.created_by}"

    def __repr__(self):
        return (f"<XrasActivationEvent(id={self.xras_activation_event_id}, "
                f"project_id={self.project_id}, event_type={self.event_type!r}, "
                f"created_by={self.created_by!r})>")


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
