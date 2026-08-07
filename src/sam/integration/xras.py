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
    Update) and as an ``NCAR####`` token for New. The two columns therefore
    diverge exactly on the New path, where a projcode is minted — which is what
    makes both worth storing.

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

    raw_payload = Column(Text, nullable=False)

    #: received | processed | manual | failed | replayed
    status = Column(String(16), nullable=False)

    #: The HTTP code we answered: 200, 400 or 422. ``status='failed'`` covers both a
    #: malformed body (400) and a schema rejection (422) — an operator triaging the
    #: log needs to tell those apart, and it stops being derivable from ``status``
    #: the moment handlers add their own validation failures.
    http_status = Column(Integer)

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


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
