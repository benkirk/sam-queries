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


#: Who wrote a row in ``xras_opportunity_allocation_type``.
#:
#: ``manual`` is a human's decision and is never overwritten; ``task:xras_sweep``
#: was derived automatically, and is the set to review or revert if the
#: derivation ever proves wrong. Spelled like ``XrasActivationEvent``'s
#: ``created_by='task:xras_notices'`` so the two read the same in a query.
SOURCE_MANUAL = 'manual'
SOURCE_SWEEP = 'task:xras_sweep'


#----------------------------------------------------------------------------
class XrasOpportunityAllocationType(Base):
    """Maps an XRAS ``opportunityId`` to the SAM allocation type it means.

    This is an actual database TABLE (not a view), and the direct analogue of
    :class:`XrasResourceRepositoryKeyResource` above: a local table mapping an
    XRAS key to a SAM entity, populated out-of-band, read at ingest, never
    written by anything that talks to the XRAS API.

    **Why it exists.** ``sam.xras.extractors`` decides a project's allocation
    type with an eleven-strategy free-text ladder over ``allocationType``,
    ``opportunityName`` and ``requestTitle``. Each strategy hardcodes a
    ``(panel, allocation_type)`` pair, and the twelve pairs it can produce
    never name ``UW``, ``WRAP`` or ``LCAP`` — the whole of facility 4. So when
    University of Wyoming eventually submits through XRAS, a ``Small`` request
    resolves to panel ``UNIV USS`` and the join **succeeds**, because that is a
    perfectly valid row. Nothing fails. The only symptom is a WNA project
    holding a UNIV projcode, because ``handlers/new.py`` draws the projcode
    series from ``allocation_type.panel.facility_id``. Projcodes are not
    undoable.

    Every other mapping gap in this stack shouts — an unmapped
    ``resourceRepositoryKey`` 422s the action and writes nothing. This one is
    silent, which is why it is worth pre-empting rather than waiting for
    evidence that cannot arrive until the day it is too late.

    **The FK is to `allocation_type_id`, not to the ``(panel, type)`` string
    pair.** That resolves the ambiguity by construction and cannot drift when a
    type is renamed.

    WARNING: **Never key this on the wire ``allocationType`` string.** Its vocabulary
    differs from SAM's and it is not unique — ``sam/schemas/forms/xras.py`` says
    so explicitly. ``opportunityId`` is the stable key, it is on 41/41 observed
    payloads, and across that corpus it is single-valued: nine ids, five
    distinct pairs, one pair each.

    ``opportunity_name`` is a snapshot for humans reading the table. It is
    deliberately **not** used for anything — ``opportunityName`` has a second,
    independent consumer in ``extractors.resolve_mnemonic_code``, which routes
    on an ``'NCAR '`` prefix, and this table must not entangle itself with that.
    """
    __tablename__ = 'xras_opportunity_allocation_type'

    __table_args__ = (
        Index('xras_opportunity_alloc_type_at_idx', 'allocation_type_id'),
    )

    opportunity_id = Column(Integer, primary_key=True, autoincrement=False)
    allocation_type_id = Column(Integer,
                                ForeignKey('allocation_type.allocation_type_id'),
                                nullable=False)
    opportunity_name = Column(String(120))
    source = Column(String(32), nullable=False, server_default=text("'manual'"))

    allocation_type = relationship('AllocationType',
                                   back_populates='xras_opportunities')

    @classmethod
    def create(cls, session, *, opportunity_id, allocation_type_id,
               opportunity_name=None, source=SOURCE_MANUAL):
        """Add one mapping row.

        WARNING: **Callers must check the row does not already exist.** This does not
        upsert, deliberately: a ``manual`` row is a human's answer to a question
        the API cannot settle — the two documented cases are in
        ``sam.xras.opportunity_types`` — and the sweep must never overwrite one.
        Insert-if-absent keeps that property without needing to inspect
        ``source`` at all.
        """
        row = cls(opportunity_id=opportunity_id,
                  allocation_type_id=allocation_type_id,
                  opportunity_name=opportunity_name,
                  source=source)
        session.add(row)
        session.flush()
        return row

    def __str__(self):
        return (f"XRAS opportunity {self.opportunity_id} -> "
                f"AllocationType {self.allocation_type_id}")

    def __repr__(self):
        return (f"<XrasOpportunityAllocationType("
                f"opportunity_id={self.opportunity_id}, "
                f"allocation_type_id={self.allocation_type_id})>")


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
       verbatim, before parsing.** MySQL parses to a normalized binary form and
       re-serializes on read: it re-sorts keys by length-then-bytewise, inserts
       whitespace, and *silently collapses duplicate keys*. Round-tripping a real
       payload reordered all 23 top-level keys and grew it 2,213 -> 2,375 bytes.
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
        Index('xras_action_log_request_id', 'request_id'),
        Index('xras_action_log_replay_fk', 'source_action_id'),
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

    #: The wire's ``actionId`` -- the only identifier for the *action*, and so
    #: the idempotency key.
    #:
    #: XRAS owns the retry, so this is **detection**, not prevention. Three
    #: identical posts otherwise produce three rows identical in every
    #: filterable column, and the cost of not noticing is asymmetric: Extension
    #: writes nothing on a repeat, Supplement adds a full increment.
    action_id = Column(Integer)

    #: The wire's ``requestId`` -- the identity of one request *line*, where
    #: ``request_number`` names the whole family (a New plus each Renewal is its
    #: own line). This is the only column that can join two rows across a mint:
    #: the corpus holds a pair byte-identical but for ``requestNumber`` (request
    #: token vs minted projcode), same ``actionId``, same ``requestId``. NULL on
    #: the RoleChange and unmapped ingresses, whose wire carries no requestId.
    request_id = Column(Integer)

    #: Which legacy service handled it — one of :data:`sam.xras.dispatch.SERVICES`.
    #: Recorded on the ``manual`` arm too, which is the whole point: four parking
    #: causes are otherwise byte-identical.
    service = Column(String(16))

    #: Why it parked or failed, in words. Deliberately **not** ``error_messages``,
    #: which means "the 422 body XRAS received" and is a wire contract.
    outcome_reason = Column(String(255))

    raw_payload = Column(Text, nullable=False)

    #: received | processed | manual | failed | replayed | unmapped
    #: (``unmapped`` is not an action state — see ``XRAS_ACTION_STATUSES``.)
    status = Column(String(16), nullable=False)

    #: The HTTP code we answered: 200, 400 or 422. ``status='failed'`` covers a
    #: malformed body (400), a schema rejection, a handler rejection and an
    #: oversized body (all 422), which an operator triaging the log must tell
    #: apart and cannot derive from ``status``.
    #:
    #: ``SmallInteger`` to match the DDL's ``SMALLINT UNSIGNED``. ``Integer`` is
    #: harmless in MySQL but is the kind of drift that makes a guard computed
    #: from the ORM quietly wrong -- pinned by
    #: ``tests/stress/test_audit_row_survives.py``.
    http_status = Column(SmallInteger)

    #: The ordered error list, one message per line — the same list the 422 carries.
    error_messages = Column(Text)

    #: Non-fatal facts the action survived, newline-joined like
    #: ``error_messages`` (which stays the 422 wire contract and must not be
    #: overloaded): a grant with no award number, an unflagged-primary fos
    #: fallback, a roster/role disagreement. utf8mb4 in the DDL — grant titles
    #: are user free text. The 2026-08-24 reversal of the decline in
    #: ``docs/xras/incoming/implemented/XRAS_STRESS_AND_SCHEMA.md``.
    warnings = Column(Text)

    projcode_result = Column(String(30))
    processed_time = Column(DateTime)
    processed_by = Column(String(35))

    source_action_id = Column(Integer, ForeignKey('xras_action_log.xras_action_log_id'))

    #: The row this one re-checks, and the re-checks of this row. A tree, not a
    #: flat list: re-checking a re-check points at what was clicked, so the lineage
    #: is preserved rather than collapsed to the root.
    source_action = relationship('XrasActionLog', remote_side=[xras_action_log_id],
                                 back_populates='rechecks')
    rechecks = relationship('XrasActionLog', back_populates='source_action')

    def __str__(self):
        return f"{self.action_type or '<unparsed>'} {self.request_number or ''} ({self.status})"

    def __repr__(self):
        return (f"<XrasActionLog(id={self.xras_action_log_id}, "
                f"action_type={self.action_type!r}, "
                f"request_number={self.request_number!r}, status={self.status!r})>")


#: The write vocabulary for ``xras_activation_event.event_type``, and the ONLY
#: enforcement point: the DDL declares a bare ``VARCHAR(16)`` on purpose, since
#: an ENUM change is a DBA ticket and a string is not. Validated in
#: :meth:`XrasActivationEvent.create`, because a typo'd event type would
#: otherwise never match the derive rule and simply vanish. Kept on the model
#: module rather than beside the UI *filter* vocabulary in
#: ``sam.queries.xras_actions``.
XRAS_ACTIVATION_EVENT_TYPES = (
    'notified',    # an operator asserted they handed the project off
    'dismissed',   # should not be activated via XRAS; clears the call to action
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
    ``webapp/api/xras/recheck.py`` §2), and ``created_by`` at ``varchar(35)``,
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
    #: deliberately unused: its ``server_default=CURRENT_TIMESTAMP`` resolves in
    #: the MySQL server's timezone (UTC in the containers) against SAM's
    #: naive-Mountain convention, and MySQL rounds fractional seconds rather
    #: than truncating. ``XrasActionLog`` makes the same choice.
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


#: ``xras_remediation_event.operation`` vocabulary — the write verbs the XRAS
#: credential actually holds, proven live (the first five 2026-08-21,
#: ``docs/xras/outgoing/XRAS_WRITE_PROBES.md``; the request-editor verbs
#: 2026-08-22, see ``docs/xras/outgoing/REQUEST_EDITOR.md`` §3/§5). Validated in
#: :meth:`XrasRemediationEvent.create` for the same reason
#: :data:`XRAS_ACTIVATION_EVENT_TYPES` is: the column is a bare ``VARCHAR`` by
#: design, so this tuple is the only thing between a typo and an audit row that
#: no query ever finds again.
XRAS_REMEDIATION_OPERATIONS = (
    'merge_person',            # destructive, user-agnostic; deletes the source
    'withdraw_action',         # de-approves one action back to Incomplete
    'submit_action',           # (re-)submits one action; lands in Under Review
    'add_role',                # puts a username on a request's roster
    'remove_role',             # takes one roleId off it
    # The request editor (Part B). All edit the Requested stage on our current
    # key (Phase 0, 2026-08-22); an admin/review key would reach Approved.
    'update_resource_amount',  # set a resource's amount (add-or-update a line)
    'remove_resource',         # delete a resource's stage line
    'set_action_dates',        # create an allocation-date range
    'update_action_dates',     # update an allocation-date range in place
    'remove_action_dates',     # delete an allocation-date range
    # The metadata editors (Part B2a, 2026-08-22). ``update_attributes`` is
    # shortened from "request attributes" to fit VARCHAR(24) — the test guards it.
    'update_attributes',       # title/shortTitle/abstract
    'update_action',           # action fields (userComments)
    # The destructive lifecycle (Part C, ADMIN_XRAS only). Irreversible in XRAS.
    'delete_request',          # delete a whole request
    'renew_request',           # spawn a renewal
    'add_action',              # add an action to a request
)

#: ``xras_remediation_event.status``. Five values, and the distinctions matter:
#:
#: ``attempted``  written *before* dispatch, so a row exists even if the process
#:                dies mid-call. Anything still ``attempted`` needs a human.
#: ``verified``   a re-read confirmed the effect. The only success.
#: ``unverified`` XRAS answered 200 and the re-read did **not** confirm it, or
#:                could not be made at all. Not the same as failure — see
#:                :class:`~sam.integration.xras_api.admin_client.XrasWriteResult`.
#: ``rejected``   XRAS refused deterministically (4xx). Nothing happened.
#: ``error``      the write itself errored. May or may not have applied.
XRAS_REMEDIATION_STATUSES = (
    'attempted', 'verified', 'unverified', 'rejected', 'error',
)


#----------------------------------------------------------------------------
class XrasRemediationEvent(Base, SessionMixin):
    """One operator write against the **XRAS** side, recorded on SAM's side.

    This is an actual database TABLE (not a view).

    Every row is an irreversible-ish thing a human did to a system SAM does not
    own: merged one XRAS identity into another (the source is *deleted*),
    de-approved an award back to a draft, or changed a request's roster. XRAS
    keeps its own history; this table is the record of **who asked for it from
    SAM, why, and what we saw happen** — which is the part XRAS cannot tell us.

    **Two identities per row, and they are not the same person.**
    ``created_by`` is the operator who clicked, at ``users.username`` width like
    every other audit table here. ``xa_user`` is who SAM *impersonated* to
    authorize the call — every request-scoped XRAS write authorizes on
    "``XA-USER`` holds a role on that request", so SAM acts as the PI. Losing
    that distinction would attribute an operator's decision to a PI who was
    never involved. ``xa_user`` is NULL for merge, which is user-agnostic.

    **Written twice, on a private session, deliberately.** The row is created
    ``attempted`` and committed *before* the call goes out, then updated on a
    fresh session once the outcome is known — both outside any request
    transaction (the ``NotificationLedger`` idiom). A 200 from XRAS cannot be
    rolled back, so the record of it must not be rollback-able either. A row
    left ``attempted`` is therefore meaningful: it says a write went out and SAM
    never learned how it ended.

    **No foreign keys, on purpose.** Every identifier here — ``request_id``,
    ``action_id``, ``role_id``, and both usernames — belongs to XRAS. A
    placeholder username is *deleted* by the merge this row records, so an FK to
    ``users`` would either fail or, worse, quietly prevent recording the very
    operation that removed it.

    ``before_state`` / ``after_state`` carry JSON captures. For a merge that
    includes the pre-merge person detail, because **merge does not copy person
    detail** — ``residenceCountry`` in particular exists nowhere else SAM can
    reach once the source is gone.

    Do not overload this with ``xras_account_event``
    (``XRAS_OUTGOING_QUERIES.md`` § 7.6): that one is username-keyed with
    state-derive semantics, reserved for its own feature. This one is a flat
    append-only log of attempts.
    """
    __tablename__ = 'xras_remediation_event'

    __table_args__ = (
        # "What has been done lately", the card's default read.
        Index('xras_remediation_event_op_time', 'operation', 'creation_time'),
        # "What happened to this person" — the merge trail.
        Index('xras_remediation_event_user', 'username'),
        # "What happened to this request" — reachable from the action log filter.
        Index('xras_remediation_event_request', 'request_number'),
        # "What did this operator do" — the accountability read.
        Index('xras_remediation_event_operator', 'created_by', 'creation_time'),
    )

    xras_remediation_event_id = Column(Integer, primary_key=True,
                                       autoincrement=True)

    #: One of :data:`XRAS_REMEDIATION_OPERATIONS`.
    operation = Column(String(24), nullable=False)

    #: One of :data:`XRAS_REMEDIATION_STATUSES`.
    status = Column(String(16), nullable=False)

    #: The XRAS username acted on — the merge source, or the person given or
    #: denied a role. Wider than ``users.username`` because ARC placeholders
    #: (``<name>-user-<token>``) are longer than any SAM account name.
    username = Column(String(64))

    #: Merge only: the identity retained.
    target_username = Column(String(64))

    #: XRAS's request number — **usually** a projcode, but not always.
    #:
    #: WARNING: wider than ``xras_action_log.request_number`` (30) on purpose.
    #: The action log only sees requests being *pushed*, which always carry a
    #: real projcode; this table sees the whole remediation cohort, including
    #: Submitted requests whose number is still free text a PI typed. Measured
    #: live, ``'New University Large Request - Fall 2017 UCUD0005 Zhong'`` is 55
    #: characters and renders with a Withdraw button, so it is reachable. At 30
    #: the insert truncates, or errors under strict mode. Stays utf8mb3 like the
    #: other identifiers, so an equality lookup against the action log is not a
    #: mixed-charset comparison.
    request_number = Column(String(128))

    #: XRAS-side ids. ``request_id`` is what the write routes key on while
    #: ``request_number`` is what the readable reports family keys on — SAM has
    #: to carry both (PRIVILEGE(#3)).
    request_id = Column(Integer)
    action_id = Column(Integer)

    #: Role ops: the roleId XRAS assigned (add) or removed (remove). Recorded
    #: because it is what an *undo* would need — role removal is keyed on the
    #: id, not the username.
    role_id = Column(Integer)

    #: The wire spelling of the role (``PI`` / ``Allocation Manager`` / ``User``).
    role_type = Column(String(24))

    #: Who SAM impersonated. NULL for user-agnostic ops. See the class docstring.
    xa_user = Column(String(64))

    #: The human who clicked. **Never** ``task:*`` — nothing here is automated,
    #: and the sweep has no business writing rows to this table.
    created_by = Column(String(35), nullable=False)

    #: Stamped from the *app* clock, never a DB default — same reasoning as
    #: :class:`XrasActivationEvent` (server default resolves in the server's
    #: timezone, SAM's convention is naive-Mountain).
    creation_time = Column(DateTime, nullable=False)

    #: When the outcome was learned. NULL means it never was.
    completed_time = Column(DateTime)

    http_status = Column(Integer)

    #: One line an operator can read: the verify verdict, or XRAS's refusal.
    outcome_reason = Column(String(255))

    #: The operator's reason. Required for withdraw — de-approving someone's
    #: award without saying why is not an audit trail.
    comment = Column(Text)

    #: JSON captures. utf8mb4: they hold free text and real names.
    before_state = Column(Text)
    after_state = Column(Text)

    @classmethod
    def create(cls, session, *, operation, created_by, username=None,
               target_username=None, request_number=None, request_id=None,
               action_id=None, role_id=None, role_type=None, xa_user=None,
               comment=None, before_state=None, status='attempted'):
        """Open the row **before** the write goes out.

        Flushes but does not commit — the caller owns the transaction, and for
        this table the caller is deliberately a private session that commits
        immediately, so the row survives whatever happens to the request.

        Args:
            session:         the (private) session to add to.
            operation:       one of :data:`XRAS_REMEDIATION_OPERATIONS`.
            created_by:      ``users.username`` of the human who clicked.
            before_state:    pre-write capture; serialized if not already a str.

        Raises:
            ValueError: unknown *operation* or *status*, or a ``task:``
                operator. The last one is not hypothetical bookkeeping — it is
                the assertion that nothing in ``src/scheduling/`` may ever write
                to XRAS, made where a row would have to be created to do so.
        """
        if operation not in XRAS_REMEDIATION_OPERATIONS:
            raise ValueError(
                f"unknown xras_remediation_event.operation {operation!r}; "
                f"expected one of {', '.join(XRAS_REMEDIATION_OPERATIONS)}")
        if status not in XRAS_REMEDIATION_STATUSES:
            raise ValueError(
                f"unknown xras_remediation_event.status {status!r}; "
                f"expected one of {', '.join(XRAS_REMEDIATION_STATUSES)}")
        if str(created_by).startswith('task:'):
            raise ValueError(
                'XRAS remediations are operator actions; a scheduled task may '
                f'never write one (created_by={created_by!r})')

        event = cls(
            operation=operation,
            status=status,
            username=username,
            target_username=target_username,
            request_number=request_number,
            request_id=request_id,
            action_id=action_id,
            role_id=role_id,
            role_type=role_type,
            xa_user=xa_user,
            created_by=str(created_by)[:35],
            comment=comment,
            before_state=_as_json_text(before_state),
            creation_time=datetime.now(),
        )
        session.add(event)
        session.flush()
        return event

    @classmethod
    def complete(cls, session, event_id, *, status, http_status=None,
                 outcome_reason=None, before_state=None, after_state=None,
                 role_id=None):
        """Close the row once the outcome is known. Returns it, or ``None``.

        WARNING: **``before_state`` is written here, not at :meth:`create`.** The
        capture is made by the client *during* the call — it re-reads the
        subject immediately before dispatching — so it does not exist yet when
        the ``attempted`` row is opened. Recording it only at open time would
        leave this column permanently NULL, which is exactly what it did until
        2026-08-21.

        Called on a **fresh** session — the one that opened the row has already
        committed and gone. ``None`` back means the row vanished, which should
        be impossible and is worth a caller's log line rather than an exception
        that would mask the write's own result.
        """
        if status not in XRAS_REMEDIATION_STATUSES:
            raise ValueError(
                f"unknown xras_remediation_event.status {status!r}; "
                f"expected one of {', '.join(XRAS_REMEDIATION_STATUSES)}")

        event = session.get(cls, event_id)
        if event is None:
            return None

        event.status = status
        event.http_status = http_status
        if outcome_reason:
            event.outcome_reason = str(outcome_reason)[:255]
        if before_state is not None:
            event.before_state = _as_json_text(before_state)
        if after_state is not None:
            event.after_state = _as_json_text(after_state)
        if role_id is not None:
            event.role_id = role_id
        event.completed_time = datetime.now()
        session.flush()
        return event

    def __str__(self):
        subject = self.username or self.request_number or '?'
        return f"{self.operation} on {subject} by {self.created_by} ({self.status})"

    def __repr__(self):
        return (f"<XrasRemediationEvent(id={self.xras_remediation_event_id}, "
                f"operation={self.operation!r}, status={self.status!r}, "
                f"created_by={self.created_by!r})>")


def _as_json_text(value):
    """Serialize a capture for storage, leaving an existing string alone.

    ``default=str`` so a stray datetime in a payload cannot turn an audit write
    into a ``TypeError`` — losing the row would be far worse than storing a
    timestamp as text.
    """
    if value is None or isinstance(value, str):
        return value
    import json
    return json.dumps(value, default=str, sort_keys=True)


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
