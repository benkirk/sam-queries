"""Disk-quota query.

``get_disk_quotas()`` reproduces the legacy Java
``GET /api/protected/admin/dasg/diskquota`` -- one record per DISK account whose
project is active, whose resource is commissioned, and which has a currently
active allocation. Consumed by DASG for per-project disk provisioning.

Path->resource resolution mirrors legacy ``DiskResourceByPathSelector``: a
project directory belongs to the disk resource whose ``root_directory`` is the
longest prefix of the directory name (``disk_resource_root_directory``).

Response shape: ``docs/apis/SYSTEMS_INTEGRATION_APIs.md``.
"""

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload, selectinload

from sam import Account, Allocation, Project, Resource, ResourceType
from sam.resources.resources import DiskResourceRootDirectory


def _latest_allocation(account: Account) -> Optional[Allocation]:
    """Legacy Account.getLatestAllocation: the null-end_date allocation if any,
    else the one with the max end_date. Ignores soft-deleted rows."""
    live = [al for al in account.allocations if not al.deleted]
    if not live:
        return None
    open_ended = [al for al in live if al.end_date is None]
    if open_ended:
        return open_ended[0]
    return max(live, key=lambda al: al.end_date)


def _resolve_resource_id(directory_name: str, roots: List[tuple]) -> Optional[int]:
    """Return the resource_id whose root_directory is the longest prefix of
    directory_name. ``roots`` is a list of (root_directory, resource_id) sorted
    longest-first."""
    for root, resource_id in roots:
        if directory_name == root or directory_name.startswith(root.rstrip('/') + '/'):
            return resource_id
    return None


def get_disk_quotas(session: Session, now: Optional[datetime] = None) -> List[Dict]:
    """Build the disk-quota list.

    Returns one dict per qualifying DISK account with keys ``projcode``,
    ``group_name``, ``data_manager``, ``resource_name``, ``quota`` (latest
    allocation amount, may be None) and ``paths`` (sorted directory names that
    resolve to this account's resource). Serialized to the legacy camelCase
    shape by ``DiskQuotaSchema``.
    """
    now = now or datetime.now()

    # Global longest-prefix table for path->resource resolution.
    roots = [
        (r.root_directory, r.resource_id)
        for r in session.query(DiskResourceRootDirectory)
                        .filter(DiskResourceRootDirectory.is_active).all()
    ]
    roots.sort(key=lambda rr: len(rr[0]), reverse=True)

    # DISK accounts: active project, commissioned resource, an active allocation.
    accounts = (
        session.query(Account)
        .join(Project, and_(Account.project_id == Project.project_id,
                            Project.is_active))
        .join(Resource, Account.resource_id == Resource.resource_id)
        .join(ResourceType, and_(Resource.resource_type_id == ResourceType.resource_type_id,
                                ResourceType.resource_type == 'DISK'))
        .join(Allocation, and_(Allocation.account_id == Account.account_id,
                              Allocation.deleted.is_(False),
                              Allocation.start_date <= now,
                              Allocation.end_date > now))
        .options(
            joinedload(Account.project).joinedload(Project.admin),
            joinedload(Account.project).joinedload(Project.lead),
            joinedload(Account.project).selectinload(Project.directories),
            joinedload(Account.resource),
            selectinload(Account.allocations),
        )
        .filter(Resource.is_active)
        .distinct()
        .all()
    )

    result: List[Dict] = []
    for account in accounts:
        project = account.project
        manager = project.admin or project.lead
        latest = _latest_allocation(account)

        paths = sorted(
            d for d in project.active_directories
            if _resolve_resource_id(d, roots) == account.resource_id
        )

        result.append({
            'projcode':      project.projcode,
            'group_name':    project.projcode.lower(),
            'data_manager':  manager.username if manager else None,
            'resource_name': account.resource.resource_name,
            'quota':         float(latest.amount) if latest and latest.amount is not None else None,
            'paths':         paths,
        })

    result.sort(key=lambda d: (d['projcode'], d['resource_name']))
    return result
