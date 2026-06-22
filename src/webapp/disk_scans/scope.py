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

from typing import List, Tuple

from sam.queries.disk_usage import build_disk_subtree

from webapp.disk_scans.session import get_module


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
