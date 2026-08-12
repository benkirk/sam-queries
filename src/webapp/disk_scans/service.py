"""Service layer for fs-scans filesystem-scan rows.

Thin wrappers around :class:`fs_scans.FsScanQueries` that take a
:class:`webapp.disk_scans.scope.ScanScope` saying which collections and
paths the caller may query.

Auth is the route's job, not the service's. What the service guarantees is
the same invariant in every mode: **a scope that resolves to no reachable
collections yields no results**, never a facade fan-out across every
collection. Passing ``ResourceScanScope`` is how a caller says "unscoped,
and I am gated on ``VIEW_ALL_FILESYSTEM_DATA``" — it can't happen by
forgetting an argument. This is the fs-scans analogue of the job service's
``JobScope``.

The fs_scans facade owns its own sessions (one per collection per call),
so there is no session context manager here — we just construct
``FsScanQueries(filesystems=…)`` and call the matching method.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from webapp.disk_scans.cache import cached_scan
from webapp.disk_scans.scope import ProjectScanScope
from webapp.utils import age_bands
from webapp.disk_scans.session import (
    collections_for_resource,
    database_for_resource,
    is_enabled,
)


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
    scope = ProjectScanScope(session, project, resource_name)
    mod, _path_prefixes, collections = scope.resolve()
    if not collections:
        return {'collections': [], 'scan_dates': {}, 'reference': None}
    q = mod.FsScanQueries(filesystems=collections, database=scope.database)
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

    The ladder maths lives in ``webapp.utils.age_bands`` because the age-range
    filter control needs the same mapping over a *span* of bands, plus its
    inverse. This function is the single-band case, and stays here because the
    band → filter-window contract is a disk-scans concept (see ``_BAND_SPECS``).

    Bounds come from the plugin's ``ATIME_BUCKETS`` day thresholds (the single
    source of truth) relative to the scan date. A directory is in band ``i``
    when its last-access *age* (days from the scan) is in ``[lower, upper)``;
    since access-time = scan − age, that maps to ``accessed_after = scan −
    upper`` (older edge; ``None`` for the open-ended oldest band) and
    ``accessed_before = scan − lower`` (newer edge; the scan date itself for
    band 0). Returns ``{}`` if the plugin or scan date is unavailable.
    """
    ladder = age_bands.atime_ladder()
    if ladder is None or not reference_scan_date or not bucket_labels:
        return {}
    wanted = set(bucket_labels)
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for i, label in enumerate(age_bands.labels(ladder)):
        if label not in wanted:
            continue
        after, before = age_bands.band_bounds(ladder, reference_scan_date, i, i)
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


# --- Entity rollups (owner / group) ----------------------------------------
#
# The two rollups are the same query shape over a different id column: fetch
# rows, collect the ids, resolve them to names, attach. One spec each rather
# than two near-identical functions.
_ENTITY_SPECS = {
    'owner': {
        'query':    'owner_summary',
        'id_field': 'owner_uid',
        'name_key': 'username',
        'resolver': 'resolve_usernames',
    },
    'group': {
        'query':    'group_summary',
        'id_field': 'owner_gid',
        'name_key': 'groupname',
        'resolver': 'resolve_groupnames',
    },
}

# --- Distribution histograms (access-time / file-size) ---------------------
#
# Same envelope, same tagging step, different band semantics. ``bounds``
# maps the plugin's bucket labels to the filter window each band represents,
# so a band click can drill into exactly the directories it covers.
_BAND_SPECS = {
    'access_history': {
        'query':  'access_history',
        'bounds': lambda hist: _atime_band_bounds(
            hist.get('reference_scan_date'), hist.get('bucket_labels')),
        'keys':   ('accessed_after', 'accessed_before'),
    },
    'file_sizes': {
        'query':  'file_size_histogram',
        'bounds': lambda hist: _size_band_bounds(hist.get('bucket_labels')),
        'keys':   ('size_min', 'size_max'),
    },
}


def scan_entity_summary(scope, kind: str, *,
                        limit: Optional[int] = 50) -> List[Dict[str, Any]]:
    """Per-owner (UID) or per-group (GID) rollup, names resolved.

    *kind* is ``'owner'`` or ``'group'``. Each row gains a ``username`` /
    ``groupname`` key (``None`` when the id can't be resolved). Returns
    ``[]`` when *scope* resolves to no reachable collections — the uniform
    "no results, never unscoped" contract.
    """
    spec = _ENTITY_SPECS[kind]
    mod, path_prefixes, collections = scope.resolve()
    if not collections:
        return []

    q = mod.FsScanQueries(filesystems=collections, database=scope.database)

    def _compute():
        rows = getattr(q, spec['query'])(path_prefixes=path_prefixes, limit=limit)
        ids = {r[spec['id_field']] for r in rows
               if r.get(spec['id_field']) is not None}
        names = getattr(q, spec['resolver'])(ids) if ids else {}
        for r in rows:
            r[spec['name_key']] = names.get(r.get(spec['id_field']))
        return rows

    # cache key tolerates an unscoped (None) prefix list — normalise to [].
    return cached_scan(kind, q, collections, path_prefixes or [],
                       {'limit': limit}, _compute, database=scope.database)


def scan_distribution(scope, kind: str, *,
                      owner_uid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Access-time or file-size histogram, bands tagged with their windows.

    *kind* is ``'access_history'`` or ``'file_sizes'``. Both return the same
    envelope (``bucket_labels``, ``buckets{label: {data, files, owners}}``,
    ``total_data``, ``total_files``, ``username_map``,
    ``reference_scan_date``, ``fast_path``) — only the band semantics
    differ. Returns ``None`` when *scope* resolves to no reachable
    collections, the plugin is unavailable, or no scan dates exist.

    Each band is tagged with the filter window it represents so the
    per-user drill-down can list exactly that band's directories.
    """
    spec = _BAND_SPECS[kind]
    mod, path_prefixes, collections = scope.resolve()
    if not collections:
        return None

    q = mod.FsScanQueries(filesystems=collections, database=scope.database)

    def _compute():
        hist = getattr(q, spec['query'])(
            path_prefixes=path_prefixes, owner_uid=owner_uid)
        if hist:
            bounds = spec['bounds'](hist)
            lo_key, hi_key = spec['keys']
            for label, b in (hist.get('buckets') or {}).items():
                if label in bounds:
                    b[lo_key] = bounds[label][lo_key]
                    b[hi_key] = bounds[label][hi_key]
        return hist

    return cached_scan(kind, q, collections, path_prefixes or [],
                       {'owner_uid': owner_uid}, _compute,
                       database=scope.database)


def scan_directories(
    scope,
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
) -> List[Dict[str, Any]]:
    """Largest directories within *scope* (sortable view).

    Returns ``[]`` when the scope resolves to no reachable collections.
    Filters (``owner_uid`` / ``accessed_before`` / ``accessed_after`` /
    ``leaves_only`` / the avg-size bounds) narrow within that scope, and any
    of them being set routes the result into the short-TTL ``'filtered'``
    cache bucket so heavy interactive exploration can't crowd the hot
    default-path entries.

    ``atime_recursive`` selects which access-time column the date filters
    compare against — the subtree max (``True``, default) or the directory's
    own files (``False``). ``outermost_only`` collapses the result to the
    topmost directories (drops any whose ancestor is also listed) — used by
    the recursive access-history drill-down to surface removable trees, not
    every nested match.
    """
    mod, path_prefixes, collections = scope.resolve()
    if not collections:
        return []

    database = scope.database
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
    rows = cached_scan(
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
    return _drop_nested(rows) if outermost_only else rows


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
