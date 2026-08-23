"""XRAS routes for the Allocations dashboard.

The XRAS feature — the action-log page and its worklist cards, the
notify/activate/dismiss lifecycle, the read-only detail modals, and the
operator write/remediation surface — split out of ``blueprint.py`` and
``xras_remediation_routes.py`` into cohesive modules on the same
``allocations_dashboard`` blueprint.

``_shared`` is the route-free helper home the route modules build on. The
route submodules (``card_routes``, ``lifecycle_routes``, ``modals``,
``remediation``) are imported for their ``@bp.route`` side effects by
``allocations/__init__.py`` — after ``blueprint`` is fully loaded, so the
handful of ``from ..blueprint import`` back-references resolve against a
complete module.
"""
