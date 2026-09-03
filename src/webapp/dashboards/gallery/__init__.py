"""
Component gallery blueprint (dev-only).

Renders each shared ``dashboards/fragments/`` macro across the theme x layout
axes — a visual reference and a render-smoke surface. Gated OFF in production by
COMPONENT_GALLERY_ENABLED; see docs/plans/implemented/DESIGN_SYSTEM_TOOLING.md.
"""

from flask import Blueprint

bp = Blueprint('component_gallery', __name__, url_prefix='/dev/gallery')

__all__ = ['bp']

from . import blueprint  # noqa: E402,F401  (route decorates bp)
