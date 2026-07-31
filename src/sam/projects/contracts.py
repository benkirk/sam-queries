#-------------------------------------------------------------------------bh-
# Common Imports:
import re

from ..base import *
#-------------------------------------------------------------------------eh-


def normalize_contract_number(value: Optional[str]) -> Optional[str]:
    """Fold the whitespace noise manual entry leaves around the hyphen.

    ``contract_number`` is free text, and operators have entered the same
    award as ``'OCE-1419584'``, ``'OCE- 1419584'`` and ``'OCE - 1419584'``.
    This is the canonical spelling-cleanup for that column: anything
    comparing two contract numbers, or comparing SAM's number to an award
    source's, goes through here.

    Deliberately NOT the same function as the two provider query builders:

    * ``awards.nsf.nsf_award_id`` extracts the bare NSF award id (the digits
      after the last hyphen) because that is what NSF's API takes as a
      parameter.
    * ``awards.usaspending`` strips punctuation entirely for the same reason.

    Both of those build *provider request parameters* and are lossy on
    purpose. This one preserves the number, only tidying it.
    """
    if not value:
        return None
    text = re.sub(r'\s*-\s*', '-', str(value).strip())
    return re.sub(r'\s+', ' ', text).upper()


def _squashed_number(value: str) -> str:
    """All whitespace removed and upper-cased — the SQL-comparable form.

    A superset of :func:`normalize_contract_number`'s folding that can be
    expressed as a MySQL ``UPPER(REPLACE(col, ' ', ''))``, so a stored value
    carrying stray spaces can still be matched. Only spaces are handled; a
    tab inside a contract number would defeat it, which has never been seen.
    """
    return re.sub(r'\s+', '', str(value)).upper()


class _Unchanged:
    """Sentinel distinguishing "leave this column alone" from "set it NULL".

    Needed only for nullable FK columns whose edit-form control submits an
    empty value when cleared — ``None`` there is a real instruction, so it
    cannot double as the "argument omitted" default.
    """

    def __repr__(self):
        return 'UNCHANGED'


UNCHANGED = _Unchanged()


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Contract(Base, TimestampMixin, DateRangeMixin, SessionMixin):
    """Funding contracts.

    ``start_date`` / ``end_date`` and the ``is_active`` hybrid come from
    ``DateRangeMixin``. Note that an expired contract does not only mean the
    grant period lapsed: unlinking the last project sets
    ``end_date = now()`` (see ``htmx_remove_project_contract``), so expiry is
    also how a contract is deactivated.
    """
    __tablename__ = 'contract'

    __table_args__ = (
        Index('contract_contract_source_fk', 'contract_source_id'),
        Index('contract_pi_user_fk', 'principal_investigator_user_id'),
        Index('contract_contract_monitor_user_fk', 'contract_monitor_user_id'),
        Index('contract_nsf_program_fk', 'nsf_program_id'),
        Index('contract_contract_number_uk', 'contract_number', unique=True),
    )

    contract_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source_id = Column(Integer, ForeignKey('contract_source.contract_source_id'),
                                nullable=False)
    contract_number = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    url = Column(String(1000))

    principal_investigator_user_id = Column(Integer, ForeignKey('users.user_id'),
                                           nullable=False)
    contract_monitor_user_id = Column(Integer, ForeignKey('users.user_id'))
    nsf_program_id = Column(Integer, ForeignKey('nsf_program.nsf_program_id'))

    contract_monitor = relationship('User', foreign_keys=[contract_monitor_user_id], back_populates='monitored_contracts')
    contract_source = relationship('ContractSource', back_populates='contracts')
    nsf_program = relationship('NSFProgram', back_populates='contracts')
    principal_investigator = relationship('User', foreign_keys=[principal_investigator_user_id], back_populates='pi_contracts')
    projects = relationship('ProjectContract', back_populates='contract')

    def update(
        self,
        *,
        title: Optional[str] = None,
        url: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        contract_monitor_user_id=UNCHANGED,
        nsf_program_id=UNCHANGED,
    ) -> 'Contract':
        """
        Update this Contract record.

        Title, url, start_date, end_date, contract monitor, and NSF program
        may be changed. PI, source, and number are read-only via this method.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            title: New title (NOT NULL)
            url: New URL (nullable — pass empty string to clear)
            start_date: New start date (NOT NULL)
            end_date: New end date — must be after start_date if both known
            contract_monitor_user_id: New monitor; pass ``None`` to clear.
                Omit (``UNCHANGED``) to leave alone — the two are distinct
                here, unlike the other fields, because these columns are
                nullable FKs and the edit form's pickers submit an empty
                value for "cleared". Callers that only touch other fields
                (e.g. the expire-contract route) must not wipe them.
            nsf_program_id: New NSF program; same ``None``/``UNCHANGED`` rule

        Returns:
            self

        Raises:
            ValueError: If validation fails
        """
        if title is not None:
            if not title.strip():
                raise ValueError("title is required")
            self.title = title.strip()

        if url is not None:
            self.url = url.strip() if url.strip() else None

        if start_date is not None:
            self.start_date = start_date

        if end_date is not None:
            effective_start = start_date or self.start_date
            if effective_start and end_date <= effective_start:
                raise ValueError("end_date must be after start_date")
            self.end_date = end_date

        if contract_monitor_user_id is not UNCHANGED:
            self.contract_monitor_user_id = contract_monitor_user_id

        if nsf_program_id is not UNCHANGED:
            self.nsf_program_id = nsf_program_id

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        contract_number: str,
        title: str,
        start_date: datetime,
        contract_source_id: int,
        principal_investigator_user_id: int,
        url: Optional[str] = None,
        end_date: Optional[datetime] = None,
        contract_monitor_user_id: Optional[int] = None,
        nsf_program_id: Optional[int] = None,
    ) -> 'Contract':
        """
        Create a new Contract.

        ``contract_monitor_user_id`` (the funding source's program manager)
        and ``nsf_program_id`` are optional: 98% and 99% of existing rows
        respectively carry one, but neither column is NOT NULL and non-NSF
        sources have no program.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not contract_number or not contract_number.strip():
            raise ValueError("contract_number is required")
        if not title or not title.strip():
            raise ValueError("title is required")
        if end_date is not None and end_date <= start_date:
            raise ValueError("end_date must be after start_date")

        obj = cls(
            contract_number=contract_number.strip(),
            title=title.strip(),
            start_date=start_date,
            end_date=end_date,
            url=url.strip() if url and url.strip() else None,
            contract_source_id=contract_source_id,
            principal_investigator_user_id=principal_investigator_user_id,
            contract_monitor_user_id=contract_monitor_user_id,
            nsf_program_id=nsf_program_id,
        )
        session.add(obj)
        session.flush()
        return obj

    # ── queries ─────────────────────────────────────────────────────────

    @classmethod
    def get_by_number(cls, session, contract_number: str) -> Optional['Contract']:
        """The contract with this exact number, or ``None``.

        ``contract_number`` carries a unique index
        (``contract_contract_number_uk``), so this is a scalar getter rather
        than a "first match".

        The column is free text and holds things like ``'OCE- 1419584'`` and
        ``'USDA Prime Award No. 2013-67003-20652'``. An exact match is tried
        first — that is the indexed path and the common case — and **only on
        a miss** does a whitespace-insensitive comparison run, so that
        ``'OCE-1419584'`` finds the row stored as ``'OCE- 1419584'``. The
        fallback is a scan, but it costs nothing when the exact lookup hits.

        A caller doing free-text search still wants :meth:`search_by_pattern`;
        this remains a scalar getter.
        """
        number = (contract_number or '').strip()
        if not number:
            return None

        hit = (session.query(cls)
               .filter(cls.contract_number == number)
               .one_or_none())
        if hit is not None:
            return hit

        # `contract_number` is uniquely indexed, so two rows can only collapse
        # to one squashed form if an operator entered the same award twice
        # with different spacing. Order for determinism rather than raising.
        return (session.query(cls)
                .filter(func.upper(func.replace(cls.contract_number, ' ', ''))
                        == _squashed_number(number))
                .order_by(cls.contract_id)
                .first())

    @classmethod
    def existing_by_number(cls, session, numbers) -> Dict[str, 'Contract']:
        """Which of *numbers* SAM already has, keyed by normalised number.

        One query for the whole set rather than one per number — this
        annotates award-search results, where the alternative is a query per
        row rendered.

        Both sides are compared with whitespace squashed out, so an award
        numbered ``'OCE-1419584'`` upstream still matches the row an operator
        stored as ``'OCE- 1419584'``. Look up with
        :func:`normalize_contract_number` applied to your number.
        """
        squashed = {_squashed_number(n) for n in numbers
                    if n and str(n).strip()}
        if not squashed:
            return {}

        rows = (session.query(cls)
                .filter(func.upper(func.replace(cls.contract_number, ' ', ''))
                        .in_(sorted(squashed)))
                .all())
        return {normalize_contract_number(c.contract_number): c for c in rows}

    @classmethod
    def search_by_pattern(cls, session, pattern: Optional[str] = None, *,
                          active_only: bool = True,
                          source: Optional[str] = None,
                          pi: Optional[str] = None,
                          monitor: Optional[str] = None,
                          program: Optional[str] = None,
                          limit: int = 50,
                          with_details: bool = False) -> List['Contract']:
        """Search contracts by number/title text and optional filters.

        **Wildcard semantics.** *pattern* is treated as a LIKE pattern iff it
        contains ``%`` or ``_``; otherwise it is substring-matched. So
        ``'climate'`` and ``'%climate%'`` agree, while ``'AGS-%'`` anchors.

        This is a deliberate divergence from two neighbours, and neither is a
        mis-port:

        * ``_apply_filter`` in ``sam/queries/charges.py`` is the same shape but
          falls back to **exact equality**, not substring, and uses ``like``
          rather than ``ilike``. Substring is the right default for a search
          box; exact is the right default for a report filter.
        * ``sam-search user --search`` advertises wildcard support and then
          strips ``%`` and ``_`` before querying, so its documented semantics
          are not its real ones. Do not propagate that.

        Args:
            pattern:      matched against ``contract_number`` OR ``title``;
                          ``None`` returns everything the filters allow.
            active_only:  restrict to contracts inside their date range.
            source:       ``contract_source`` name, e.g. ``'NSF'`` (exact).
            pi:           principal investigator username (exact).
            monitor:      contract monitor username (exact).
            program:      ``nsf_program_name`` — pattern-matched like
                          *pattern*, since program names are long.
            limit:        row cap.
            with_details: eager-load ``contract_source`` and
                          ``principal_investigator``. Off by default so FK
                          pickers, which read neither, do not pay for them.
        """
        # Neither is in sam.base's star import; User is a cross-domain import
        # kept local to avoid a core<->projects cycle at module load.
        from sqlalchemy.orm import aliased, selectinload

        from sam.core.users import User

        def _text_filter(column, value):
            """LIKE iff the term carries a wildcard, else substring."""
            return (column.ilike(value) if ('%' in value or '_' in value)
                    else column.ilike(f'%{value}%'))

        query = session.query(cls)

        if with_details:
            query = query.options(
                selectinload(cls.contract_source),
                selectinload(cls.principal_investigator),
            )

        if pattern and pattern.strip():
            term = pattern.strip()
            query = query.filter(or_(_text_filter(cls.contract_number, term),
                                     _text_filter(cls.title, term)))

        if active_only:
            query = query.filter(cls.is_active)

        if source:
            query = (query.join(ContractSource)
                     .filter(ContractSource.contract_source == source.strip()))

        if program:
            term = program.strip()
            query = (query.join(NSFProgram)
                     .filter(_text_filter(NSFProgram.nsf_program_name, term)))

        # Two user FKs on one table, so each needs its own alias.
        for username, fk in ((pi, cls.principal_investigator_user_id),
                             (monitor, cls.contract_monitor_user_id)):
            if username:
                person = aliased(User)
                query = (query.join(person, fk == person.user_id)
                         .filter(person.username == username.strip()))

        return query.order_by(cls.contract_number).limit(limit).all()

    def __str__(self):
        return f"{self.contract_number}: {self.title[:50]}..."

    def __repr__(self):
        return f"<Contract(number='{self.contract_number}', title='{self.title[:50]}...')>"

    def __eq__(self, other):
        """Two contracts are equal if they have the same contract_id."""
        if not isinstance(other, Contract):
            return False
        return self.contract_id is not None and self.contract_id == other.contract_id

    def __hash__(self):
        """Hash based on contract_id for set/dict operations."""
        return hash(self.contract_id) if self.contract_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class ContractSource(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Sources of funding contracts."""
    __tablename__ = 'contract_source'

    __table_args__ = (
        Index('contract_source_contract_source_uk', 'contract_source', unique=True),
    )

    contract_source_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source = Column(String(50), nullable=False)

    contracts = relationship('Contract', back_populates='contract_source')

    def update(
        self,
        *,
        contract_source: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'ContractSource':
        """
        Update this ContractSource record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            contract_source: New source name (NOT NULL, unique)
            active: Whether the source is active

        Returns:
            self

        Raises:
            ValueError: If name is empty
        """
        if contract_source is not None:
            if not contract_source.strip():
                raise ValueError("contract_source name is required")
            self.contract_source = contract_source.strip()

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        contract_source: str,
    ) -> 'ContractSource':
        """
        Create a new ContractSource.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not contract_source or not contract_source.strip():
            raise ValueError("contract_source name is required")

        obj = cls(contract_source=contract_source.strip())
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.contract_source}"

    def __repr__(self):
        return f"<ContractSource(source='{self.contract_source}')>"


#----------------------------------------------------------------------------
class ProjectContract(Base):
    """Links projects to funding contracts."""
    __tablename__ = 'project_contract'

    __table_args__ = (
        Index('project_contract_project_fk', 'project_id'),
        Index('project_contract_contract_fk', 'contract_id'),
        Index('project_id_contract_id_uk', 'project_id', 'contract_id', unique=True),
    )

    project_contract_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    contract_id = Column(Integer, ForeignKey('contract.contract_id'), nullable=False)
    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    project = relationship('Project', back_populates='contracts')
    contract = relationship('Contract', back_populates='projects')

    @classmethod
    def create(cls, session, *, project_id: int, contract_id: int) -> 'ProjectContract':
        """Link a project to a funding contract.

        Does NOT commit; caller must wrap in management_transaction().
        Note: removal requires session.delete(pc) since this model has no
        soft-delete column.  If the contract has no other project links,
        the caller should also call contract.update(end_date=...) to
        deactivate the Contract record.
        """
        obj = cls(project_id=project_id, contract_id=contract_id)
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        projcode = self.project.projcode if self.project else self.project_id
        contract_num = self.contract.contract_number if self.contract else self.contract_id
        return f"{projcode} / {contract_num}"

    def __repr__(self):
        return f"<ProjectContract(id={self.project_contract_id}, project_id={self.project_id}, contract_id={self.contract_id})>"


# ============================================================================
# Role/Permission Management
# ============================================================================


#----------------------------------------------------------------------------
class NSFProgram(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """NSF program classifications."""
    __tablename__ = 'nsf_program'

    __table_args__ = (
        Index('nsf_program_name_uk', 'nsf_program_name', unique=True),
    )

    nsf_program_id = Column(Integer, primary_key=True, autoincrement=True)
    nsf_program_name = Column(String(255), nullable=False)

    contracts = relationship('Contract', back_populates='nsf_program')

    def update(
        self,
        *,
        nsf_program_name: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'NSFProgram':
        """
        Update this NSFProgram record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            nsf_program_name: New program name (NOT NULL, unique)
            active: Whether the program is active

        Returns:
            self

        Raises:
            ValueError: If name is empty
        """
        if nsf_program_name is not None:
            if not nsf_program_name.strip():
                raise ValueError("nsf_program_name is required")
            self.nsf_program_name = nsf_program_name.strip()

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        nsf_program_name: str,
    ) -> 'NSFProgram':
        """
        Create a new NSFProgram.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not nsf_program_name or not nsf_program_name.strip():
            raise ValueError("nsf_program_name is required")

        obj = cls(nsf_program_name=nsf_program_name.strip())
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.nsf_program_name}"

    def __repr__(self):
        return f"<NSFProgram(name='{self.nsf_program_name}')>"


#-------------------------------------------------------------------------em-
