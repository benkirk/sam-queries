"""
Charge summary management functions.

Write operations for comp_charge_summary, disk_charge_summary, and
archive_charge_summary tables. These replace the Java/Hibernate batch
process that previously populated these tables.

All functions use session.flush() (not commit). Callers must wrap in
management_transaction() for proper commit/rollback handling.

Update semantics: PUT-style full replacement. All mutable fields are set
to whatever the caller provides; omitting an optional field writes NULL.
Callers needing partial updates must first read the existing row and merge.

WARNING: The upsert functions use query-then-insert (not atomic
ON DUPLICATE KEY UPDATE). The summary tables have no UNIQUE constraint on
their natural key columns, so concurrent writes for the same natural key
(date, user, project, ...) bucket may produce duplicate rows. Batch
processes must serialize writes for the same natural key.
"""
from datetime import date
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from sam.core.users import User
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.resources.machines import Machine, Queue
from sam.accounting.accounts import Account
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


def _resolve_user(session: Session, act_username: str, act_unix_uid: Optional[int]) -> User:
    """
    Look up user by username; fall back to unix_uid if username not found.
    act_unix_uid may be None for jobs submitted before identity sync.
    Raises ValueError if neither lookup succeeds.
    """
    user = User.get_by_username(session, act_username)
    if not user and act_unix_uid is not None:
        user = session.query(User).filter_by(unix_uid=act_unix_uid).first()
    if not user:
        uid_str = f"unix_uid={act_unix_uid}" if act_unix_uid is not None else "no uid"
        raise ValueError(
            f"User '{act_username}' ({uid_str}) not found in SAM"
        )
    return user


def _resolve_project(session: Session, act_projcode: str) -> Project:
    """Look up project by projcode. Raises ValueError if not found."""
    project = Project.get_by_projcode(session, act_projcode)
    if not project:
        raise ValueError(f"Project '{act_projcode}' not found in SAM")
    return project


def _resolve_resource(session: Session, resource_name: str) -> Resource:
    """Look up resource by name. Raises ValueError if not found."""
    resource = Resource.get_by_name(session, resource_name)
    if not resource:
        raise ValueError(f"Resource '{resource_name}' not found in SAM")
    return resource


def _resolve_account(
    session: Session,
    project: Project,
    resource: Resource,
    include_deleted: bool = False,
) -> Account:
    """
    Look up account linking project to resource.
    Pass include_deleted=True for historical backfill against accounts that
    have since been marked deleted.
    Raises ValueError if no matching account exists.
    """
    account = Account.get_by_project_and_resource(
        session,
        project.project_id,
        resource.resource_id,
        exclude_deleted=not include_deleted,
    )
    if not account:
        qualifier = " (including deleted)" if include_deleted else ""
        raise ValueError(
            f"No account{qualifier} found for project '{project.projcode}' "
            f"on resource '{resource.resource_name}'"
        )
    return account


def _resolve_machine(
    session: Session, machine_name: str, resource: Resource
) -> Machine:
    """Look up machine by name within resource. Raises ValueError if not found."""
    machine = session.query(Machine).filter_by(
        name=machine_name, resource_id=resource.resource_id
    ).first()
    if not machine:
        raise ValueError(
            f"Machine '{machine_name}' not found on resource '{resource.resource_name}'"
        )
    return machine


def _resolve_facility_name(project: Project) -> Optional[str]:
    """Derive facility_name from project.allocation_type.panel.facility.
    Returns None if any link in the chain is missing."""
    at = project.allocation_type
    if at and at.panel and at.panel.facility:
        return at.panel.facility.facility_name
    return None


def _resolve_machine_optional(
    session: Session, machine_name: Optional[str], resource: Resource
) -> Machine:
    """Resolve machine by name, or auto-resolve if resource has exactly one machine.
    Raises ValueError if machine_name is None and resource has 0 or 2+ machines."""
    if machine_name is not None:
        return _resolve_machine(session, machine_name, resource)

    machines = resource.machines
    if len(machines) == 0:
        raise ValueError(
            f"Resource '{resource.resource_name}' has no machines; "
            f"machine_name must be provided explicitly"
        )
    if len(machines) == 1:
        return machines[0]

    names = sorted(m.name for m in machines)
    raise ValueError(
        f"Resource '{resource.resource_name}' has {len(machines)} machines "
        f"({', '.join(names)}); machine_name must be provided explicitly"
    )


def _resolve_or_create_queue(
    session: Session,
    queue_name: str,
    resource: Resource,
    create_if_missing: bool,
) -> Queue:
    """
    Look up queue by name within resource.
    If not found and create_if_missing=True, auto-creates the queue.
    If not found and create_if_missing=False, raises ValueError with actionable hint.
    """
    queue = session.query(Queue).filter_by(
        queue_name=queue_name, resource_id=resource.resource_id
    ).first()
    if not queue:
        if not create_if_missing:
            raise ValueError(
                f"Queue '{queue_name}' not found on resource '{resource.resource_name}'. "
                f"Pass create_queue_if_missing=True to create it automatically."
            )
        queue = Queue(
            queue_name=queue_name,
            resource_id=resource.resource_id,
            description=f"Auto-created from charge summary import",
            wall_clock_hours_limit=12,
        )
        session.add(queue)
        session.flush()  # Populate queue_id before referencing it
    return queue


def upsert_comp_charge_summary(
    session: Session,
    *,
    activity_date: date,
    act_username: str,
    act_projcode: str,
    act_unix_uid: Optional[int],
    resource_name: str,
    machine_name: Optional[str] = None,
    queue_name: str,
    num_jobs: int,
    core_hours: float,
    charges: float,
    # Optional resolved overrides (default to act_ values if omitted)
    username: Optional[str] = None,
    projcode: Optional[str] = None,
    unix_uid: Optional[int] = None,
    resource: Optional[str] = None,
    facility_name: Optional[str] = None,
    cos: Optional[int] = None,
    sweep: Optional[int] = None,
    error_comment: Optional[str] = None,
    create_queue_if_missing: bool = False,
    include_deleted_accounts: bool = False,
) -> Tuple[CompChargeSummary, str]:
    """
    Insert or update a CompChargeSummary row.

    Entity resolution order (stops on first error):
      1. User  (_resolve_user)
      2. Project  (_resolve_project)
      3. Resource  (_resolve_resource)
      4. Account  (_resolve_account)
      5. Machine  (_resolve_machine)
      6. Queue  (_resolve_or_create_queue)

    Returns:
        Tuple of (record, action) where action is 'created' or 'updated'.

    Raises:
        ValueError: If any required entity is not found.

    Notes:
        Does NOT commit. Caller must use management_transaction().
        PUT semantics: all mutable fields replaced; omitted optionals write NULL.
        Pass include_deleted_accounts=True for historical charge backfill.
    """
    user = _resolve_user(session, act_username, act_unix_uid)
    project = _resolve_project(session, act_projcode)
    res = _resolve_resource(session, resource_name)
    account = _resolve_account(session, project, res, include_deleted=include_deleted_accounts)
    machine = _resolve_machine_optional(session, machine_name, res)
    queue = _resolve_or_create_queue(session, queue_name, res, create_queue_if_missing)

    # `act_unix_uid` mirrors the resolved user's uid when the caller didn't
    # supply one (act_username / act_projcode are required so they are
    # always populated). Keeps the as-reported column populated whenever
    # the user is known.
    eff_act_unix_uid = act_unix_uid if act_unix_uid is not None else (
        user.unix_uid if user is not None else None
    )

    # Resolved field defaults — explicit None checks preserve falsy values (e.g. uid=0)
    resolved_username = username if username is not None else act_username
    resolved_projcode = projcode if projcode is not None else act_projcode
    resolved_unix_uid = unix_uid if unix_uid is not None else eff_act_unix_uid
    resource_col = resource if resource is not None else resource_name

    # Facility name: use caller-provided value; fall back to project chain
    if facility_name is None:
        facility_name = _resolve_facility_name(project)

    # Natural key lookup (use machine.name since machine_name may have been auto-resolved)
    existing = session.query(CompChargeSummary).filter(
        CompChargeSummary.activity_date == activity_date,
        CompChargeSummary.act_username == act_username,
        CompChargeSummary.act_projcode == act_projcode,
        CompChargeSummary.machine == machine.name,
        CompChargeSummary.queue == queue_name,
        CompChargeSummary.resource == resource_col,
    ).first()

    if existing is None:
        # INSERT — set all fields including immutable act_ fields
        record = CompChargeSummary(
            activity_date=activity_date,
            act_username=act_username,
            act_projcode=act_projcode,
            act_unix_uid=eff_act_unix_uid,
            username=resolved_username,
            projcode=resolved_projcode,
            unix_uid=resolved_unix_uid,
            user_id=user.user_id,
            account_id=account.account_id,
            facility_name=facility_name,
            machine=machine.name,
            machine_id=machine.machine_id,
            queue=queue_name,
            queue_id=queue.queue_id,
            resource=resource_col,
            num_jobs=num_jobs,
            core_hours=core_hours,
            charges=charges,
            cos=cos,
            sweep=sweep,
            error_comment=error_comment,
        )
        session.add(record)
        action = 'created'
    else:
        # UPDATE — overwrite only mutable fields; NEVER touch act_ fields
        existing.username = resolved_username
        existing.projcode = resolved_projcode
        existing.unix_uid = resolved_unix_uid
        existing.user_id = user.user_id
        existing.account_id = account.account_id
        existing.facility_name = facility_name
        existing.machine_id = machine.machine_id
        existing.queue_id = queue.queue_id
        existing.num_jobs = num_jobs
        existing.core_hours = core_hours
        existing.charges = charges
        existing.cos = cos
        existing.sweep = sweep
        existing.error_comment = error_comment
        record = existing
        action = 'updated'

    session.flush()
    return record, action


def _upsert_storage_summary(
    session: Session,
    model_cls,        # DiskChargeSummary or ArchiveChargeSummary
    *,
    activity_date: date,
    act_username: Optional[str],
    act_projcode: Optional[str],
    act_unix_uid: Optional[int],
    resource_name: str,
    charges: float,
    number_of_files: Optional[int] = None,
    bytes: Optional[int] = None,
    terabyte_years: Optional[float] = None,
    username: Optional[str] = None,
    projcode: Optional[str] = None,
    unix_uid: Optional[int] = None,
    facility_name: Optional[str] = None,
    include_deleted_accounts: bool = False,
    # Pre-resolved overrides — when supplied, the matching resolver call is
    # skipped. Used by the disk-charging gap-row path (synthetic
    # `<unidentified>` audit rows pointing at the project lead).
    user: Optional[User] = None,
    project: Optional[Project] = None,
    account: Optional[Account] = None,
) -> Tuple[object, str]:
    """Shared upsert logic for DiskChargeSummary and ArchiveChargeSummary.

    When ``user``, ``project``, or ``account`` is supplied, the corresponding
    resolver is skipped. This lets callers attribute synthetic rows
    (e.g. ``act_username='<unidentified>'``) to a guaranteed-existing User
    without that user actually existing under the audit name.
    """
    if user is None:
        user = _resolve_user(session, act_username, act_unix_uid)
    res = _resolve_resource(session, resource_name)
    if account is None:
        if project is None:
            project = _resolve_project(session, act_projcode)
        account = _resolve_account(session, project, res, include_deleted=include_deleted_accounts)
    elif project is None:
        # Derive project from the pre-resolved account (avoids needing a
        # valid act_projcode when callers already know the account).
        project = account.project

    # `act_*` fields mirror the resolved entity when the caller did NOT
    # supply a raw value. Keeps the as-reported / resolved columns from
    # going asymmetric — e.g. legacy disk feeds populate `username` /
    # `projcode` but leave `act_unix_uid` NULL even though the user is
    # fully resolved. Use these effective values for both the natural-
    # key lookup and the inserted row so upserts stay idempotent.
    eff_act_username = act_username if act_username is not None else (
        user.username if user is not None else None
    )
    eff_act_projcode = act_projcode if act_projcode is not None else (
        project.projcode if project is not None else None
    )
    eff_act_unix_uid = act_unix_uid if act_unix_uid is not None else (
        user.unix_uid if user is not None else None
    )

    # Resolved field defaults — explicit None checks preserve falsy values (e.g. uid=0)
    resolved_username = username if username is not None else (
        user.username if user is not None else eff_act_username
    )
    resolved_projcode = projcode if projcode is not None else (
        project.projcode if project is not None else eff_act_projcode
    )
    resolved_unix_uid = unix_uid if unix_uid is not None else (
        user.unix_uid if user is not None and act_unix_uid is None else eff_act_unix_uid
    )

    # Facility name: use caller-provided value; fall back to project chain
    if facility_name is None:
        facility_name = _resolve_facility_name(project)

    # Natural key: (activity_date, act_username, act_projcode, account_id)
    existing = session.query(model_cls).filter(
        model_cls.activity_date == activity_date,
        model_cls.act_username == eff_act_username,
        model_cls.act_projcode == eff_act_projcode,
        model_cls.account_id == account.account_id,
    ).first()

    if existing is None:
        record = model_cls(
            activity_date=activity_date,
            act_username=eff_act_username,
            act_projcode=eff_act_projcode,
            act_unix_uid=eff_act_unix_uid,
            username=resolved_username,
            projcode=resolved_projcode,
            unix_uid=resolved_unix_uid,
            user_id=user.user_id,
            account_id=account.account_id,
            facility_name=facility_name,
            number_of_files=number_of_files,
            bytes=bytes,
            terabyte_years=terabyte_years,
            charges=charges,
        )
        session.add(record)
        action = 'created'
    else:
        existing.username = resolved_username
        existing.projcode = resolved_projcode
        existing.unix_uid = resolved_unix_uid
        existing.user_id = user.user_id
        existing.account_id = account.account_id
        existing.facility_name = facility_name
        existing.number_of_files = number_of_files
        existing.bytes = bytes
        existing.terabyte_years = terabyte_years
        existing.charges = charges
        record = existing
        action = 'updated'

    session.flush()
    return record, action


def upsert_disk_charge_summary(session: Session, **kwargs) -> Tuple[DiskChargeSummary, str]:
    """Insert or update a DiskChargeSummary row. Delegates to _upsert_storage_summary."""
    return _upsert_storage_summary(session, DiskChargeSummary, **kwargs)


def upsert_archive_charge_summary(session: Session, **kwargs) -> Tuple[ArchiveChargeSummary, str]:
    """Insert or update an ArchiveChargeSummary row. Delegates to _upsert_storage_summary."""
    return _upsert_storage_summary(session, ArchiveChargeSummary, **kwargs)
