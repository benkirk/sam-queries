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

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from webapp.disk_scans.cache import cached_scan
from webapp.disk_scans.scope import resolve_scan_scope
from webapp.disk_scans.session import (
    collections_for_resource,
    database_for_resource,
    get_module,
    is_enabled,
)


def _scoped(
    session,
    project,
    resource_name: str,
    subpath: Optional[str] = None,
) -> Tuple[Optional[Any], List[str], List[str]]:
    """Resolve ``(module, path_prefixes, collections)`` for a scan query.

    ``collections`` is intersected with the warmed/reachable set so we
    never construct a ``FsScanQueries`` for a schema that isn't there (on
    the SQLite backend that would create an empty ``*.db`` and silently
    return zero rows). Returns ``(mod, path_prefixes, [])`` — an empty
    collection list — whenever the query would otherwise be unscoped or
    unsatisfiable; callers MUST treat an empty collection list as "no
    results" and not call the facade.

    ``subpath`` narrows the scope to a fileset path. Matching is done in
    NORMALIZED (mount-stripped) space via ``mod.normalize_path`` so it works
    whether ``subpath`` arrives absolute (the disk page's ``?fileset=``, a
    ``ProjectDirectory`` path) or already normalized (the explorer's row /
    breadcrumb drill, which surfaces normalized scan paths — see
    ``project_fs_scans_paths_normalized``). Two in-scope cases:

      * **Selection** — ``subpath`` equals / is an ancestor of the project's
        filesets: keep the project prefixes at or under it (pick among the
        project's own directories).
      * **Descent** — ``subpath`` is a descendant of a project prefix (a real
        subdirectory below a registered fileset): query that deeper subtree.

    Anything outside the project's scope yields no results, so this can never
    widen beyond what the project owns. Narrowing to a non-root sub-path
    defeats the whole-collection-root fast path, so this is the inherently slow
    on-the-fly query (callers lazy-load it).
    """
    mod = get_module()
    if mod is None:
        return None, [], []

    path_prefixes, collections = resolve_scan_scope(session, project, resource_name)

    # Fileset drill-down, matched in normalized space, then recompute the
    # owning collections from what survives.
    if subpath:
        s = mod.normalize_path(subpath).rstrip('/')
        norm = {p: mod.normalize_path(p).rstrip('/') for p in path_prefixes}
        under = [p for p, pn in norm.items() if pn == s or pn.startswith(s + '/')]
        if under:                                              # selection
            path_prefixes = under
        elif any(s == pn or s.startswith(pn + '/') for pn in norm.values()):
            path_prefixes = [s]                                # descent
        else:
            path_prefixes = []                                 # out of scope
        collections = sorted({
            coll
            for p in path_prefixes
            if (coll := mod.collection_for_path(p)) is not None
        })

    # Keep only collections that are actually reachable for THIS resource's
    # database (the warmed set behind resource_name) — not the global union, so
    # a collection name shared across databases can't leak across resources.
    collections = [c for c in collections
                   if c in set(collections_for_resource(resource_name))]
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


def scan_overview(session, project, resource_name: str) -> Dict[str, Any]:
    """Page-render summary for the Filesystem Scans card header.

    Returns the warmed collections *project* spans on *resource_name* plus
    their latest scan dates — enough for the caller to (a) decide whether to
    show the card (``collections`` non-empty) and (b) render a "scanned
    <date>" freshness badge in the header without lazy-loading a tab.

    ``{'collections': [...], 'scan_dates': {collection: datetime|None},
       'reference': datetime|None}`` — ``reference`` is the most recent scan
    date across the project's collections. Cheap: one scoped subtree build
    plus one ``scan_metadata`` lookup per collection (1-2 in practice).
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name)
    if not collections:
        return {'collections': [], 'scan_dates': {}, 'reference': None}
    q = mod.FsScanQueries(filesystems=collections,
                          database=database_for_resource(resource_name))
    scan_dates = {}
    for c in collections:
        dates = q.scan_dates(filesystems=[c])
        scan_dates[c] = max(dates) if dates else None
    reference = max((d for d in scan_dates.values() if d), default=None)
    return {'collections': collections, 'scan_dates': scan_dates, 'reference': reference}


def _drop_nested(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only the outermost directories — drop any row whose ancestor path
    is also present.

    Used by the access-history *recursive* drill-down, where the question is
    "which whole trees are entirely stale?": listing both ``/foo`` and
    ``/foo/bar`` is redundant — you'd reclaim the tree by deleting ``/foo``.
    Rows arrive sorted by recursive size, and an ancestor's subtree is always
    larger than its descendant's, so an ancestor always precedes its children;
    a single forward pass keeping the first-seen prefix is therefore exact.
    """
    kept: List[Dict[str, Any]] = []
    kept_paths: List[str] = []
    for r in rows:
        p = (r.get('path') or '').rstrip('/')
        if any(p == kp or p.startswith(kp + '/') for kp in kept_paths):
            continue
        kept.append(r)
        kept_paths.append(p)
    return kept


def _atime_band_bounds(reference_scan_date, bucket_labels) -> Dict[str, Dict[str, Optional[str]]]:
    """Map each access-history band to ``(accessed_after, accessed_before)``
    ``YYYY-MM-DD`` date strings, so the band → user → directories drill-down can
    filter directories to exactly the clicked band's date window.

    Bounds come from the plugin's ``ATIME_BUCKETS`` day thresholds (the single
    source of truth, imported here) relative to the scan date. A directory is
    in band ``i`` when its last-access *age* (days from the scan) is in
    ``[lower, upper)``; since access-time = scan − age, that maps to
    ``accessed_after = scan − upper`` (older edge; ``None`` for the open-ended
    oldest band) and ``accessed_before = scan − lower`` (newer edge; the scan
    date itself for band 0). Returns ``{}`` if the plugin or scan date is
    unavailable.
    """
    try:
        from fs_scans.core.models import ATIME_BUCKETS
    except Exception:
        return {}
    if not reference_scan_date or not bucket_labels:
        return {}
    wanted = set(bucket_labels)
    out: Dict[str, Dict[str, Optional[str]]] = {}
    prev = 0  # cumulative lower threshold (days) carried across bands in order
    for label, upper in ATIME_BUCKETS:
        lower = prev
        if upper is not None:
            prev = upper
        if label not in wanted:
            continue
        before = (reference_scan_date - timedelta(days=lower)).strftime('%Y-%m-%d')
        after = (None if upper is None
                 else (reference_scan_date - timedelta(days=upper)).strftime('%Y-%m-%d'))
        out[label] = {'accessed_after': after, 'accessed_before': before}
    return out


def _size_band_bounds(bucket_labels) -> Dict[str, Dict[str, Optional[int]]]:
    """Map each file-size band to its ``(size_min, size_max)`` average-file-size
    bounds (bytes), so the band → user → directories drill-down can filter
    directories by average own-file size.

    Bounds come from the plugin's ``SIZE_BUCKETS`` (label, min, max) — the single
    source of truth — mapped by label. The largest band's ``max`` is ``None``
    (open-ended). Returns ``{}`` if the plugin is unavailable.
    """
    try:
        from fs_scans.core.models import SIZE_BUCKETS
    except Exception:
        return {}
    if not bucket_labels:
        return {}
    wanted = set(bucket_labels)
    return {
        label: {'size_min': mn, 'size_max': mx}
        for label, mn, mx in SIZE_BUCKETS
        if label in wanted
    }


def _scan_directories(
    mod,
    collections: List[str],
    path_prefixes: Optional[List[str]],
    *,
    sort_by: str = 'size',
    limit: Optional[int] = 50,
    owner_uid: Optional[int] = None,
    owner_gid: Optional[int] = None,
    accessed_before: Optional[datetime] = None,
    accessed_after: Optional[datetime] = None,
    atime_recursive: bool = True,
    min_avg_size: Optional[int] = None,
    max_avg_size: Optional[int] = None,
    leaves_only: bool = False,
    single_owner: bool = False,
    min_depth: Optional[int] = None,
    max_depth: Optional[int] = None,
    database: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Mode-agnostic directory query core shared by project + resource modes.

    Callers resolve ``(mod, collections, path_prefixes)`` first — project mode
    via :func:`_scoped` (refuses unscoped), resource mode via
    :func:`collections_for_resource` (deliberately unscoped, ``path_prefixes``
    may be ``None`` for the whole-collection fast path). ``database`` selects
    the CNPG database (resolved from the resource by the caller). Any of
    ``owner_uid / accessed_before / accessed_after / leaves_only`` being set
    routes the result into the short-TTL ``'filtered'`` cache bucket so heavy
    interactive exploration can't crowd the hot default-path entries.
    """
    q = mod.FsScanQueries(filesystems=collections, database=database)
    filtered = bool(owner_uid is not None or owner_gid is not None
                    or accessed_before or accessed_after or leaves_only
                    or min_avg_size is not None or max_avg_size is not None)
    opts = {
        'sort_by': sort_by, 'limit': limit,
        'owner_uid': owner_uid,
        'owner_gid': owner_gid,
        'accessed_before': accessed_before.isoformat() if accessed_before else None,
        'accessed_after': accessed_after.isoformat() if accessed_after else None,
        'atime_recursive': atime_recursive,
        'min_avg_size': min_avg_size,
        'max_avg_size': max_avg_size,
        'leaves_only': leaves_only,
        'single_owner': single_owner,
        'min_depth': min_depth, 'max_depth': max_depth,
    }
    return cached_scan(
        'directories', q, collections,
        # cache key tolerates an unscoped (None) prefix list — normalise to [].
        path_prefixes or [], opts,
        database=database,
        compute=lambda: q.list_directories(
            path_prefixes=path_prefixes,
            sort_by=sort_by,
            limit=limit,
            owner_id=owner_uid,
            group_id=owner_gid,
            accessed_before=accessed_before,
            accessed_after=accessed_after,
            atime_recursive=atime_recursive,
            min_avg_size=min_avg_size,
            max_avg_size=max_avg_size,
            leaves_only=leaves_only,
            single_owner=single_owner,
            min_depth=min_depth,
            max_depth=max_depth,
        ),
        bucket='filtered' if filtered else 'default',
    )


def scan_directories(
    session,
    project,
    resource_name: str,
    *,
    sort_by: str = 'size',
    limit: Optional[int] = 50,
    owner_uid: Optional[int] = None,
    owner_gid: Optional[int] = None,
    accessed_before: Optional[datetime] = None,
    accessed_after: Optional[datetime] = None,
    atime_recursive: bool = True,
    min_avg_size: Optional[int] = None,
    max_avg_size: Optional[int] = None,
    outermost_only: bool = False,
    leaves_only: bool = False,
    single_owner: bool = False,
    min_depth: Optional[int] = None,
    max_depth: Optional[int] = None,
    subpath: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Largest directories for *project* on *resource_name* (sortable view).

    **Project mode.** Always scoped to the project's directory paths (optionally
    narrowed to one fileset via *subpath*). The unscoped-refusal safety
    invariant is intact: returns ``[]`` when the project has no scannable
    directories or the plugin is unavailable. Filters
    (``owner_uid / accessed_before / accessed_after / leaves_only``) narrow
    within that scope.

    ``atime_recursive`` selects which access-time column the date filters
    compare against — the subtree max (``True``, default) or the directory's own
    files (``False``). ``outermost_only`` collapses the result to the topmost
    directories (drops any whose ancestor is also listed) — used by the
    recursive access-history drill-down to surface removable trees, not every
    nested match.
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name, subpath)
    if not collections:
        return []
    rows = _scan_directories(
        mod, collections, path_prefixes,
        sort_by=sort_by, limit=limit,
        owner_uid=owner_uid, owner_gid=owner_gid, accessed_before=accessed_before,
        accessed_after=accessed_after, atime_recursive=atime_recursive,
        min_avg_size=min_avg_size, max_avg_size=max_avg_size,
        leaves_only=leaves_only,
        single_owner=single_owner, min_depth=min_depth, max_depth=max_depth,
        database=database_for_resource(resource_name),
    )
    return _drop_nested(rows) if outermost_only else rows


def scan_directories_resource(
    resource_name: str,
    *,
    subpath: Optional[str] = None,
    sort_by: str = 'size',
    limit: Optional[int] = 50,
    owner_uid: Optional[int] = None,
    owner_gid: Optional[int] = None,
    accessed_before: Optional[datetime] = None,
    accessed_after: Optional[datetime] = None,
    atime_recursive: bool = True,
    min_avg_size: Optional[int] = None,
    max_avg_size: Optional[int] = None,
    outermost_only: bool = False,
    leaves_only: bool = False,
) -> List[Dict[str, Any]]:
    """Largest directories across an **entire disk resource**, unscoped.

    **Resource mode** (elevated). Deliberately *not* project-scoped — it
    browses every collection the resource owns
    (:func:`collections_for_resource`). With no *subpath* it queries the whole
    collection (``path_prefixes=None`` → the facade's fast path); a *subpath*
    drills into a single fileset (the slow on-the-fly scan). This bypasses the
    project-scoping safety invariant by design, so it is only ever reachable
    behind the ``VIEW_ALL_FILESYSTEM_DATA``-gated route. Returns ``[]`` when the
    plugin is unavailable or the resource maps to no reachable collections.

    Accepts the same filter set as the project-scoped :func:`scan_directories`
    so the whole-FS card's per-user / per-group / histogram-band drill-downs
    (which re-target this fragment with ``owner_uid`` / ``owner_gid`` /
    ``min_avg_size`` / ``recursive`` etc.) filter identically.
    """
    mod = get_module()
    if mod is None:
        return []
    collections = collections_for_resource(resource_name)
    if not collections:
        return []
    path_prefixes = [subpath] if subpath else None
    rows = _scan_directories(
        mod, collections, path_prefixes,
        sort_by=sort_by, limit=limit,
        owner_uid=owner_uid, owner_gid=owner_gid,
        accessed_before=accessed_before, accessed_after=accessed_after,
        atime_recursive=atime_recursive,
        min_avg_size=min_avg_size, max_avg_size=max_avg_size,
        leaves_only=leaves_only,
        database=database_for_resource(resource_name),
    )
    return _drop_nested(rows) if outermost_only else rows


def _owner_summary(
    mod, collections: List[str], path_prefixes: Optional[List[str]],
    *, limit: Optional[int], database: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Mode-agnostic per-owner (UID) rollup core, usernames resolved.

    Callers resolve ``(mod, collections, path_prefixes)`` first — project mode
    via :func:`_scoped` (always scoped), resource mode via
    :func:`collections_for_resource` (``path_prefixes=None`` → whole-collection
    fast path). ``database`` selects the CNPG database. Each row gains a
    ``username`` key (``None`` if unresolvable).
    """
    q = mod.FsScanQueries(filesystems=collections, database=database)

    def _compute():
        rows = q.owner_summary(path_prefixes=path_prefixes, limit=limit)
        uids = {r['owner_uid'] for r in rows if r.get('owner_uid') is not None}
        names = q.resolve_usernames(uids) if uids else {}
        for r in rows:
            r['username'] = names.get(r.get('owner_uid'))
        return rows

    # cache key tolerates an unscoped (None) prefix list — normalise to [].
    return cached_scan('owner', q, collections, path_prefixes or [],
                       {'limit': limit}, _compute, database=database)


def _group_summary(
    mod, collections: List[str], path_prefixes: Optional[List[str]],
    *, limit: Optional[int], database: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Mode-agnostic per-group (GID) rollup core, group names resolved."""
    q = mod.FsScanQueries(filesystems=collections, database=database)

    def _compute():
        rows = q.group_summary(path_prefixes=path_prefixes, limit=limit)
        gids = {r['owner_gid'] for r in rows if r.get('owner_gid') is not None}
        names = q.resolve_groupnames(gids) if gids else {}
        for r in rows:
            r['groupname'] = names.get(r.get('owner_gid'))
        return rows

    return cached_scan('group', q, collections, path_prefixes or [],
                       {'limit': limit}, _compute, database=database)


def _access_history(
    mod, collections: List[str], path_prefixes: Optional[List[str]],
    *, owner_uid: Optional[int], database: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mode-agnostic access-time histogram core (band date windows tagged)."""
    q = mod.FsScanQueries(filesystems=collections, database=database)

    def _compute():
        hist = q.access_history(path_prefixes=path_prefixes, owner_uid=owner_uid)
        if hist:
            # Tag each band with the date window it represents so the per-user
            # drill-down can list that band's directories (see _atime_band_bounds).
            bounds = _atime_band_bounds(hist.get('reference_scan_date'),
                                        hist.get('bucket_labels'))
            for label, b in (hist.get('buckets') or {}).items():
                if label in bounds:
                    b['accessed_after'] = bounds[label]['accessed_after']
                    b['accessed_before'] = bounds[label]['accessed_before']
        return hist

    return cached_scan(
        'access_history', q, collections, path_prefixes or [],
        {'owner_uid': owner_uid}, _compute, database=database,
    )


def _file_sizes(
    mod, collections: List[str], path_prefixes: Optional[List[str]],
    *, owner_uid: Optional[int], database: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mode-agnostic file-size histogram core (avg-size windows tagged)."""
    q = mod.FsScanQueries(filesystems=collections, database=database)

    def _compute():
        hist = q.file_size_histogram(path_prefixes=path_prefixes, owner_uid=owner_uid)
        if hist:
            # Tag each band with its average-file-size window so the per-user
            # drill-down can list that band's directories (see _size_band_bounds).
            bounds = _size_band_bounds(hist.get('bucket_labels'))
            for label, b in (hist.get('buckets') or {}).items():
                if label in bounds:
                    b['size_min'] = bounds[label]['size_min']
                    b['size_max'] = bounds[label]['size_max']
        return hist

    return cached_scan(
        'file_sizes', q, collections, path_prefixes or [],
        {'owner_uid': owner_uid}, _compute, database=database,
    )


def _resource_collections(resource_name: str):
    """Resolve ``(mod, collections, path_prefixes)`` for resource mode.

    The unscoped analogue of :func:`_scoped`: ``collections`` come from the
    whole resource (:func:`collections_for_resource`) and ``path_prefixes`` is
    ``None`` (whole-collection fast path) unless *subpath* drills in. Returns
    ``(mod, [])`` whenever the plugin is unavailable or the resource maps to no
    reachable collections; callers MUST treat an empty list as "no results".
    """
    mod = get_module()
    if mod is None:
        return None, []
    return mod, collections_for_resource(resource_name)


def scan_owner_summary(
    session,
    project,
    resource_name: str,
    *,
    limit: Optional[int] = 50,
    subpath: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-owner (UID) rollup for *project*, with usernames resolved.

    Always scoped to the project's directory paths (optionally narrowed to
    one fileset via *subpath*). Each row gains a ``username`` key (``None``
    if the UID can't be resolved).
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name, subpath)
    if not collections:
        return []
    return _owner_summary(mod, collections, path_prefixes, limit=limit,
                          database=database_for_resource(resource_name))


def scan_group_summary(
    session,
    project,
    resource_name: str,
    *,
    limit: Optional[int] = 50,
    subpath: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-group (GID) rollup for *project*, with group names resolved.

    Always scoped to the project's directory paths (optionally narrowed to
    one fileset via *subpath*). Each row gains a ``groupname`` key (``None``
    if the GID can't be resolved).
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name, subpath)
    if not collections:
        return []
    return _group_summary(mod, collections, path_prefixes, limit=limit,
                          database=database_for_resource(resource_name))


def scan_access_history(
    session,
    project,
    resource_name: str,
    *,
    owner_uid: Optional[int] = None,
    subpath: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Access-time histogram dict for *project* (or ``None``).

    Always scoped to the project's directory paths (optionally narrowed to
    one fileset via *subpath*). Returns ``None`` when the project has no
    scannable directories, the plugin is unavailable, or no scan dates
    exist for the targeted collections. The returned dict already carries
    its own ``username_map`` for rendering.
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name, subpath)
    if not collections:
        return None
    return _access_history(mod, collections, path_prefixes, owner_uid=owner_uid,
                           database=database_for_resource(resource_name))


def scan_file_sizes(
    session,
    project,
    resource_name: str,
    *,
    owner_uid: Optional[int] = None,
    subpath: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """File-size histogram dict for *project* (or ``None``).

    Identical contract to :func:`scan_access_history` — same return shape
    (``bucket_labels``, ``buckets{label:{data,files,owners}}``,
    ``total_data``, ``total_files``, ``username_map``, ``reference_scan_date``,
    ``fast_path``) — but the buckets are file-size bands rather than
    access-time bands. Always scoped to the project's directory paths
    (optionally narrowed to one fileset via *subpath*). Returns ``None`` when
    the project has no scannable directories, the plugin is unavailable, or
    no scan dates exist for the targeted collections.
    """
    mod, path_prefixes, collections = _scoped(session, project, resource_name, subpath)
    if not collections:
        return None
    return _file_sizes(mod, collections, path_prefixes, owner_uid=owner_uid,
                       database=database_for_resource(resource_name))


# ── Resource-mode (whole-filesystem) siblings ──────────────────────────────
# Unscoped, elevated analogues of the project-scoped functions above. Each
# obtains its collections from the whole resource and runs ``path_prefixes=None``
# (the plugin's whole-collection fast path) unless a *subpath* drills in. Only
# ever reachable behind the ``VIEW_ALL_FILESYSTEM_DATA``-gated resource routes;
# return empty when the plugin is unavailable or the resource has no collections.

def scan_owner_summary_resource(
    resource_name: str,
    *,
    limit: Optional[int] = 50,
    subpath: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-owner (UID) rollup across an **entire disk resource**, unscoped."""
    mod, collections = _resource_collections(resource_name)
    if not collections:
        return []
    path_prefixes = [subpath] if subpath else None
    return _owner_summary(mod, collections, path_prefixes, limit=limit,
                          database=database_for_resource(resource_name))


def scan_group_summary_resource(
    resource_name: str,
    *,
    limit: Optional[int] = 50,
    subpath: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-group (GID) rollup across an **entire disk resource**, unscoped."""
    mod, collections = _resource_collections(resource_name)
    if not collections:
        return []
    path_prefixes = [subpath] if subpath else None
    return _group_summary(mod, collections, path_prefixes, limit=limit,
                          database=database_for_resource(resource_name))


def scan_access_history_resource(
    resource_name: str,
    *,
    owner_uid: Optional[int] = None,
    subpath: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Access-time histogram across an **entire disk resource**, unscoped."""
    mod, collections = _resource_collections(resource_name)
    if not collections:
        return None
    path_prefixes = [subpath] if subpath else None
    return _access_history(mod, collections, path_prefixes, owner_uid=owner_uid,
                           database=database_for_resource(resource_name))


def scan_file_sizes_resource(
    resource_name: str,
    *,
    owner_uid: Optional[int] = None,
    subpath: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """File-size histogram across an **entire disk resource**, unscoped."""
    mod, collections = _resource_collections(resource_name)
    if not collections:
        return None
    path_prefixes = [subpath] if subpath else None
    return _file_sizes(mod, collections, path_prefixes, owner_uid=owner_uid,
                       database=database_for_resource(resource_name))


def scan_capable_resources(app=None) -> List[str]:
    """Configured disk resources that currently have warmed scan collections.

    Reads the explicit ``FS_SCAN_RESOURCES`` config list (resource *names*,
    not IDs) and keeps only those the plugin can actually serve right now —
    so a misconfigured entry (or the whole plugin being off) never renders an
    empty Status subtab. Returns ``[]`` when the plugin is disabled. This is
    what gates the Status "Filesystem Scans" tab's visibility + subtab set.
    """
    if not is_enabled(app):
        return []
    names = (app or current_app).config.get('FS_SCAN_RESOURCES') or []
    return [n for n in names if collections_for_resource(n, app)]
