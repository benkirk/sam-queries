"""Map a SAM project to the filesystem paths + fs-scans collections it owns.

This is the scoping linchpin for the fs-scans integration: every scan
query is restricted to *a project's directories*, never run unscoped.
The source of truth is the ``project_directory`` table
(:class:`sam.projects.projects.ProjectDirectory` — ``project_id`` FK plus
the full mount-prefixed ``directory_name``).

We reuse the disk dashboard's :func:`sam.queries.disk_usage.build_disk_subtree`
so the scope matches the existing disk resource-details page exactly:
the named project **plus all active descendant projects** on the given
disk resource. From that subtree we collect every active fileset path and
derive the minimal set of fs-scans collections (PostgreSQL schemas) to
query, via the plugin's ``collection_for_path`` helper.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sam.queries.disk_usage import build_disk_subtree

from webapp.disk_scans.session import (
    collections_for_resource,
    database_for_resource,
    get_module,
)
from webapp.utils.scope import NavigatorScope


def _collect_fileset_paths(node) -> List[str]:
    """Walk a ``build_disk_subtree`` node, returning every fileset path.

    Mirrors ``webapp/dashboards/user/blueprint.py:_collect_directory_to_projcode``
    but keeps only the ``ProjectDirectory.directory_name`` strings (we don't
    need the projcode mapping here). Covers the whole active subtree.
    """
    paths = list(node.get('fileset_paths', []))
    for child in node.get('children', []):
        paths.extend(_collect_fileset_paths(child))
    return paths


def resolve_scan_scope_grouped(session, project, resource_name: str) -> List[dict]:
    """Return the scope's defining directories grouped by owning project.

    A list of ``{'projcode': str, 'is_root': bool, 'paths': [str, ...]}`` dicts,
    pre-order from *project* (the scan root) through its active descendant
    projects, each carrying its sorted ``ProjectDirectory.directory_name``
    values. Only nodes that actually own a fileset appear. Empty list when the
    project has no scannable directories.

    Powers the explorer's "Scope" panel so a user can see exactly which
    directories — and which descendant projects — bound the listing. Mirrors
    :func:`resolve_scan_scope` (same ``build_disk_subtree`` walk), but keeps the
    per-project grouping instead of flattening to a path set.
    """
    full = build_disk_subtree(session, project, resource_name)
    groups: List[dict] = []
    _collect_scope_groups(full['tree'], project.projcode, groups)
    return groups


def _collect_scope_groups(node, root_projcode: str, out: List[dict]) -> None:
    """Pre-order walk collecting one group per fileset-owning node."""
    paths = sorted(node.get('fileset_paths', []))
    if paths:
        pc = node.get('projcode')
        out.append({'projcode': pc, 'is_root': pc == root_projcode, 'paths': paths})
    for child in node.get('children', []):
        _collect_scope_groups(child, root_projcode, out)


def resolve_scan_scope(session, project, resource_name: str) -> Tuple[List[str], List[str]]:
    """Return ``(path_prefixes, collections)`` for *project* on a disk resource.

    ``path_prefixes`` — the sorted, de-duplicated full
    ``ProjectDirectory.directory_name`` values for the project's active
    subtree on ``resource_name`` (e.g. ``/glade/campaign/cisl/csg``). These
    are handed verbatim to the fs-scans facade, which normalizes mount
    prefixes internally.

    ``collections`` — the sorted, de-duplicated set of fs-scans collection
    schemas those paths map to (e.g. ``['aiml', 'cisl']``), derived via the
    plugin's ``collection_for_path``. Used to build
    ``FsScanQueries(filesystems=collections)`` so the query targets only the
    owning collections instead of fanning out across all of them.

    Both lists are empty when the project has no scannable directories (or
    the plugin is unavailable) — the caller treats that as "nothing to
    show", never as an unscoped query. The returned collections are NOT
    validated against what's reachable; the service layer intersects them
    with the warmed collection set before querying.
    """
    full = build_disk_subtree(session, project, resource_name)
    path_prefixes = sorted(set(_collect_fileset_paths(full['tree'])))

    mod = get_module()
    if mod is None or not path_prefixes:
        return path_prefixes, []

    collections = sorted({
        coll
        for p in path_prefixes
        if (coll := mod.collection_for_path(p)) is not None
    })
    return path_prefixes, collections


# ---------------------------------------------------------------------------
# Scan scopes — which collections + paths a fragment may query
# ---------------------------------------------------------------------------

class ScanScope(NavigatorScope):
    """A resolved fs-scans query target: collections, paths, database.

    The three modes used to fan out into eight service functions
    (``scan_x`` / ``scan_x_resource``) that differed only in how they
    reached ``(module, path_prefixes, collections)``. That resolution is
    :meth:`resolve` now, and the safety invariant is uniform: **an empty
    collection list means "no results"** — never an unscoped query. Callers
    check ``collections`` and bail rather than calling the facade.

    ``subpath`` narrows to one directory subtree in every mode; ``database``
    is the CNPG database backing the resource, threaded into every
    ``FsScanQueries`` so a collection name shared across databases can't
    leak across resources.
    """

    def __init__(self, resource_name: str, subpath: Optional[str] = None):
        self.resource_name = resource_name
        self.subpath = subpath or None

    @property
    def database(self) -> Optional[str]:
        return database_for_resource(self.resource_name)

    #: Owner UID pinned server-side (user mode only); ``None`` elsewhere.
    forced_owner_uid: Optional[int] = None

    def resolve(self):
        """Return ``(module, path_prefixes, collections)``.

        ``path_prefixes`` may be ``None`` — the plugin's whole-collection
        fast path — where the mode doesn't restrict to particular paths.
        """
        raise NotImplementedError

    def context(self) -> Dict[str, Any]:
        return {'mode': self.mode, 'resource_name': self.resource_name,
                'fileset': self.subpath}


class ProjectScanScope(ScanScope):
    """The directories one project owns on a disk resource."""

    mode = 'project'

    def __init__(self, session, project, resource_name: str,
                 subpath: Optional[str] = None):
        super().__init__(resource_name, subpath)
        self.session = session
        self.project = project

    def resolve(self):
        """Resolve the project's paths, narrowed by ``subpath``.

        ``collections`` is intersected with the warmed/reachable set so we
        never construct a ``FsScanQueries`` for a schema that isn't there (on
        the SQLite backend that would create an empty ``*.db`` and silently
        return zero rows). Returns an empty collection list whenever the
        query would otherwise be unscoped or unsatisfiable.

        ``subpath`` matching is done in NORMALIZED (mount-stripped) space via
        ``mod.normalize_path`` so it works whether the value arrives absolute
        (the disk page's ``?fileset=``, a ``ProjectDirectory`` path) or
        already normalized (the explorer's row / breadcrumb drill, which
        surfaces normalized scan paths). Two in-scope cases:

          * **Selection** — ``subpath`` equals / is an ancestor of the
            project's filesets: keep the project prefixes at or under it.
          * **Descent** — ``subpath`` is a descendant of a project prefix (a
            real subdirectory below a registered fileset): query that deeper
            subtree.

        Anything outside the project's scope yields no results, so this can
        never widen beyond what the project owns. Narrowing to a non-root
        sub-path defeats the whole-collection-root fast path, so that is the
        inherently slow on-the-fly query (callers lazy-load it).
        """
        mod = get_module()
        if mod is None:
            return None, [], []

        path_prefixes, collections = resolve_scan_scope(
            self.session, self.project, self.resource_name)

        if self.subpath:
            s = mod.normalize_path(self.subpath).rstrip('/')
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

        # Keep only collections actually reachable for THIS resource's
        # database (the warmed set behind resource_name) — not the global
        # union, so a collection name shared across databases can't leak
        # across resources.
        collections = [c for c in collections
                       if c in set(collections_for_resource(self.resource_name))]
        keep = set(collections)

        # Drop any path whose collection isn't queryable. This excludes paths
        # on other resources AND stale directories that don't map to a live
        # scan collection (e.g. decommissioned /glade/p/* project space).
        # Besides being correct scoping, it lets the facade's
        # whole-collection-root fast path engage: without a stray non-root
        # prefix in the set, a lab-parent project whose paths collapse to the
        # collection root reads the pre-computed tables instead of an
        # on-the-fly full-collection scan.
        path_prefixes = [p for p in path_prefixes
                         if mod.collection_for_path(p) in keep]

        if not path_prefixes or not collections:
            return mod, path_prefixes, []
        return mod, path_prefixes, collections

    def context(self) -> Dict[str, Any]:
        return {**super().context(), 'projcode': self.project.projcode}


class ResourceScanScope(ScanScope):
    """An entire disk resource. SECURITY: unscoped.

    Only ever reachable behind a ``VIEW_ALL_FILESYSTEM_DATA``-gated route —
    there is no fallback pinning here, by design. ``path_prefixes`` is
    ``None`` (the plugin's whole-collection fast path) unless a *subpath*
    drills in.
    """

    mode = 'resource'

    def resolve(self):
        mod = get_module()
        if mod is None:
            return None, None, []
        collections = collections_for_resource(self.resource_name)
        prefixes = [self.subpath] if self.subpath else None
        return mod, prefixes, collections


class UserScanScope(ResourceScanScope):
    """One user's files across a resource ("My Data").

    Reachable with ``@login_required`` alone, so the owner is pinned
    server-side: the routes pass :attr:`forced_owner_uid` into the query and
    ignore any client-supplied ``?owner_uid``. The collection/path resolution
    is the resource one — the *owner* filter is what narrows it.
    """

    mode = 'user'

    def __init__(self, resource_name: str, owner_uid: Optional[int],
                 subpath: Optional[str] = None):
        super().__init__(resource_name, subpath)
        self.forced_owner_uid = owner_uid

    def context(self) -> Dict[str, Any]:
        return {**super().context(), 'forced_owner_uid': self.forced_owner_uid}
