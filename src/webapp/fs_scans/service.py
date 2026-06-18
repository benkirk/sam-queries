"""Service layer for fs-scans filesystem-scan rows.

Thin wrappers around :class:`fs_scans.FsScanQueries` that always scope
results to a SAM project's directories (via :func:`resolve_scan_scope`)
and target only the collections that project actually lives in.

Auth is the route's job, not the service's — but, exactly like
``webapp/jobs/service.py``, the service **refuses to issue an unscoped
query**: if a project resolves to no ``path_prefixes`` (or none of its
collections are reachable) the wrappers return an empty result rather
than letting the facade fan out across every collection. This is the
fs-scans analogue of the job service pinning ``Job.account``.

The fs_scans facade owns its own sessions (one per collection per call),
so there is no session context manager here — we just construct
``FsScanQueries(filesystems=…)`` and call the matching method.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from webapp.fs_scans.scope import resolve_scan_scope
from webapp.fs_scans.session import get_collections, get_module


def _scoped(session, project, resource_name: str) -> Tuple[Optional[Any], List[str], List[str]]:
    """Resolve ``(module, path_prefixes, collections)`` for a scan query.

    ``collections`` is intersected with the warmed/reachable set so we
    never construct a ``FsScanQueries`` for a schema that isn't there (on
    the SQLite backend that would create an empty ``*.db`` and silently
    return zero rows). Returns ``(mod, path_prefixes, [])`` — an empty
    collection list — whenever the query would otherwise be unscoped or
    unsatisfiable; callers MUST treat an empty collection list as "no
    results" and not call the facade.
    """
    mod = get_module()
    if mod is None:
        return None, [], []

    path_prefixes, collections = resolve_scan_scope(session, project, resource_name)

    # Keep only collections that are actually reachable (the warmed set).
    collections = [c for c in collections if c in set(get_collections())]
    keep = set(collections)

    # Drop any path whose collection isn't queryable. This excludes paths on
    # other resources AND stale directories that don't map to a live scan
    # collection (e.g. decommissioned /glade/p/* project space). Besides being
    # correct scoping, it lets the facade's whole-collection-root fast path
    # engage: without a stray non-root prefix in the set, a lab-parent project
    # whose paths collapse to the collection root reads the pre-computed
    # tables instead of an on-the-fly full-collection scan.
    path_prefixes = [p for p in path_prefixes if mod.collection_for_path(p) in keep]

    if not path_prefixes or not collections:
        return mod, path_prefixes, []
    return mod, path_prefixes, collections


def scan_directories(
    session,
    project,
    resource_name: str,
    *,
    sort_by: str = 'size',
    limit: Optional[int] = 50,
    single_owner: bool = False,
    min_depth: Optional[int] = None,
    max_depth: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Largest directories for *project* on *resource_name* (sortable view).

    Always scoped to the project's directory paths. Returns ``[]`` when the
    project has no scannable directories or the plugin is unavailable.
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name)
    if not collections:
        return []
    q = mod.FsScanQueries(filesystems=collections)
    return q.list_directories(
        path_prefixes=path_prefixes,
        sort_by=sort_by,
        limit=limit,
        single_owner=single_owner,
        min_depth=min_depth,
        max_depth=max_depth,
    )


def scan_owner_summary(
    session,
    project,
    resource_name: str,
    *,
    limit: Optional[int] = 50,
) -> List[Dict[str, Any]]:
    """Per-owner (UID) rollup for *project*, with usernames resolved.

    Always scoped to the project's directory paths. Each row gains a
    ``username`` key (``None`` if the UID can't be resolved).
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name)
    if not collections:
        return []
    q = mod.FsScanQueries(filesystems=collections)
    rows = q.owner_summary(path_prefixes=path_prefixes, limit=limit)
    uids = {r['owner_uid'] for r in rows if r.get('owner_uid') is not None}
    names = q.resolve_usernames(uids) if uids else {}
    for r in rows:
        r['username'] = names.get(r.get('owner_uid'))
    return rows


def scan_group_summary(
    session,
    project,
    resource_name: str,
    *,
    limit: Optional[int] = 50,
) -> List[Dict[str, Any]]:
    """Per-group (GID) rollup for *project*, with group names resolved.

    Always scoped to the project's directory paths. Each row gains a
    ``groupname`` key (``None`` if the GID can't be resolved).
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name)
    if not collections:
        return []
    q = mod.FsScanQueries(filesystems=collections)
    rows = q.group_summary(path_prefixes=path_prefixes, limit=limit)
    gids = {r['owner_gid'] for r in rows if r.get('owner_gid') is not None}
    names = q.resolve_groupnames(gids) if gids else {}
    for r in rows:
        r['groupname'] = names.get(r.get('owner_gid'))
    return rows


def scan_access_history(
    session,
    project,
    resource_name: str,
    *,
    owner_uid: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Access-time histogram dict for *project* (or ``None``).

    Always scoped to the project's directory paths. Returns ``None`` when
    the project has no scannable directories, the plugin is unavailable, or
    no scan dates exist for the targeted collections. The returned dict
    already carries its own ``username_map`` for rendering.
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name)
    if not collections:
        return None
    q = mod.FsScanQueries(filesystems=collections)
    return q.access_history(path_prefixes=path_prefixes, owner_uid=owner_uid)
