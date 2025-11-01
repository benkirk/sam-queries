from sam_models_jobs import *

# ============================================================================
# Query Helper Functions
# ============================================================================

def get_job_with_activities(session, job_id: str, machine: str,
                            job_idx: int = -1,
                            era_part_key: int = 99) -> Optional[CompJob]:
    """
    Retrieve a job with all its activities.

    Args:
        session: SQLAlchemy session
        job_id: Job identifier
        machine: Machine name
        job_idx: Job index (default -1)
        era_part_key: Era partition key (default 99)

    Returns:
        CompJob instance with activities loaded, or None if not found

    Example:
        >>> job = get_job_with_activities(session, '12345', 'Derecho')
        >>> if job:
        ...     for activity in job.activities:
        ...         print(f"Util {activity.util_idx}: {activity.core_hours} hours")
    """
    from sqlalchemy.orm import joinedload

    # Need to find the submit_time first to complete the composite key
    job = session.query(CompJob).options(
        joinedload(CompJob.activities)
    ).filter(
        CompJob.job_id == job_id,
        CompJob.machine == machine,
        CompJob.job_idx == job_idx,
        CompJob.era_part_key == era_part_key
    ).first()

    return job


def get_user_charge_summary(session, user_id: int,
                            start_date: datetime,
                            end_date: datetime,
                            resource: Optional[str] = None) -> List[CompChargeSummary]:
    """
    Get charge summary for a user within a date range.

    Args:
        session: SQLAlchemy session
        user_id: User ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter (e.g., 'Derecho')

    Returns:
        List of CompChargeSummary records ordered by date

    Example:
        >>> summaries = get_user_charge_summary(
        ...     session, 12345,
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31)
        ... )
        >>> total = sum(s.charges for s in summaries)
    """
    query = session.query(CompChargeSummary).filter(
        CompChargeSummary.user_id == user_id,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    return query.order_by(CompChargeSummary.activity_date).all()


def get_project_usage_summary(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              resource: Optional[str] = None) -> Dict[str, float]:
    """
    Get aggregated usage summary for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code (e.g., 'UCUB0001')
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter (e.g., 'Derecho')

    Returns:
        Dictionary with keys:
        - total_jobs: Number of jobs
        - total_core_hours: Total core hours consumed
        - total_charges: Total charges incurred
        - average_charge_per_job: Average charge per job
        - average_core_hours_per_job: Average core hours per job

    Example:
        >>> summary = get_project_usage_summary(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     resource='Derecho'
        ... )
        >>> print(f"Project used {summary['total_core_hours']:.2f} core hours")
    """
    from sqlalchemy import func as sql_func

    query = session.query(
        sql_func.sum(CompChargeSummary.num_jobs).label('total_jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('total_core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('total_charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    result = query.first()

    total_jobs = result.total_jobs or 0
    total_core_hours = result.total_core_hours or 0.0
    total_charges = result.total_charges or 0.0

    return {
        'total_jobs': total_jobs,
        'total_core_hours': total_core_hours,
        'total_charges': total_charges,
        'average_charge_per_job': (total_charges / total_jobs) if total_jobs > 0 else 0.0,
        'average_core_hours_per_job': (total_core_hours / total_jobs) if total_jobs > 0 else 0.0
    }


def get_daily_usage_trend(session, projcode: str,
                         start_date: datetime,
                         end_date: datetime,
                         resource: Optional[str] = None) -> List[Dict[str, any]]:
    """
    Get daily usage trend for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter

    Returns:
        List of dicts with keys: date, jobs, core_hours, charges
        Ordered by date ascending

    Example:
        >>> trend = get_daily_usage_trend(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 1, 31)
        ... )
        >>> for day in trend:
        ...     print(f"{day['date']}: {day['charges']} charges")
    """
    from sqlalchemy import func as sql_func

    query = session.query(
        sql_func.date(CompChargeSummary.activity_date).label('date'),
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    query = query.group_by(
        sql_func.date(CompChargeSummary.activity_date)
    ).order_by(
        sql_func.date(CompChargeSummary.activity_date)
    )

    results = query.all()

    return [
        {
            'date': row.date,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]


def get_recent_jobs_for_project(session, projcode: str,
                               limit: int = 100,
                               resource: Optional[str] = None) -> List[CompActivityCharge]:
    """
    Get recent jobs for a project using the charge view.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        limit: Maximum number of jobs to return (default 100)
        resource: Optional machine filter (e.g., 'Derecho')

    Returns:
        List of CompActivityCharge view records ordered by submit time (descending)

    Example:
        >>> jobs = get_recent_jobs_for_project(session, 'UCUB0001', limit=50)
        >>> for job in jobs:
        ...     print(f"{job.job_id}: {job.core_hours} hours, {job.charge} charged")
    """
    query = session.query(CompActivityCharge).filter(
        CompActivityCharge.projcode == projcode
    )

    if resource:
        query = query.filter(CompActivityCharge.machine == resource)

    return query.order_by(
        CompActivityCharge.submit_time.desc()
    ).limit(limit).all()


def get_queue_usage_breakdown(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              machine: Optional[str] = None) -> List[Dict[str, any]]:
    """
    Get usage breakdown by queue for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        machine: Optional machine filter

    Returns:
        List of dicts with keys: queue, machine, jobs, core_hours, charges
        Ordered by charges descending

    Example:
        >>> breakdown = get_queue_usage_breakdown(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     machine='Derecho'
        ... )
        >>> for queue in breakdown:
        ...     print(f"{queue['queue']}: {queue['jobs']} jobs")
    """
    from sqlalchemy import func as sql_func

    query = session.query(
        CompChargeSummary.queue,
        CompChargeSummary.machine,
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if machine:
        query = query.filter(CompChargeSummary.machine == machine)

    results = query.group_by(
        CompChargeSummary.queue,
        CompChargeSummary.machine
    ).order_by(
        sql_func.sum(CompChargeSummary.charges).desc()
    ).all()

    return [
        {
            'queue': row.queue,
            'machine': row.machine,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]


def get_user_usage_on_project(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              limit: int = 10) -> List[Dict[str, any]]:
    """
    Get top users by usage on a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        limit: Maximum number of users to return (default 10)

    Returns:
        List of dicts with keys: username, user_id, jobs, core_hours, charges
        Ordered by charges descending

    Example:
        >>> top_users = get_user_usage_on_project(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     limit=5
        ... )
        >>> for user in top_users:
        ...     print(f"{user['username']}: {user['charges']:.2f}")
    """
    from sqlalchemy import func as sql_func

    results = session.query(
        CompChargeSummary.username,
        CompChargeSummary.user_id,
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    ).group_by(
        CompChargeSummary.username,
        CompChargeSummary.user_id
    ).order_by(
        sql_func.sum(CompChargeSummary.charges).desc()
    ).limit(limit).all()

    return [
        {
            'username': row.username,
            'user_id': row.user_id,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]



# ============================================================================
# End of Computational Activity and Charges section
# ============================================================================
