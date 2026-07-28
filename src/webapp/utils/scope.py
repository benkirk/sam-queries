"""Tree-scope resolution — the ``?scope=`` query parameter, in one place.

Every project-scoped surface (resource details, its subtree partials, the
fs-scans and job-history fragments) lets the user re-root the analysis at a
descendant project by passing ``?scope=<projcode>``. The validation rule is
the same everywhere and is a **security boundary**: an out-of-tree or unknown
scope must silently fall back to the authorized root project, never widen
beyond the tree the route decorator authorized.

That rule had been transcribed seven times. It lives here now.
"""

from __future__ import annotations

from typing import List, Optional

from flask import request

from sam.projects.projects import Project
from webapp.extensions import db


def resolve_scope_project(project, scope: Optional[str] = None) -> Project:
    """Resolve ``?scope=`` to a project inside *project*'s tree.

    Args:
        project: the authorized root — whatever the access decorator resolved.
        scope: an explicit projcode. ``None`` (the default) reads ``?scope=``
            from the current request.

    Returns:
        The scoped :class:`Project`, or *project* itself when the scope is
        absent, equal to the root, unknown, or belongs to another tree. Never
        raises and never returns something outside *project*'s tree, so the
        caller can use the result unconditionally.
    """
    if scope is None:
        scope = request.args.get('scope') or ''
    scope = scope.strip()

    if not scope or scope == project.projcode:
        return project

    candidate = Project.get_by_projcode(db.session, scope)
    if candidate is None or candidate.tree_root != project.tree_root:
        return project
    return candidate


def resolve_scope_projcodes(project, scope: Optional[str] = None) -> List[str]:
    """Expand a tree scope into the projcodes to query.

    The scoped project plus all its descendants when it has children, else
    just itself. Used wherever a query spans a subtree (usage rollups, the
    per-job ``account IN (...)`` filter). Invalid scopes fall back to the
    root exactly as :func:`resolve_scope_project` does.
    """
    scope_project = resolve_scope_project(project, scope)
    if scope_project.has_children:
        return [p.projcode for p in scope_project.get_descendants(include_self=True)]
    return [scope_project.projcode]
