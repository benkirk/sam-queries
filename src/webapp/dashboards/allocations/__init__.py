"""
Allocations dashboard blueprint.

Provides admin/staff dashboard for viewing allocation summaries across
all projects, resources, and facilities.
"""

from flask import Blueprint

bp = Blueprint('allocations_dashboard', __name__, url_prefix='/allocations')

__all__ = ['bp']

from . import blueprint  # noqa: E402,F401  (non-XRAS routes decorate bp)
from .xras import card_routes  # noqa: E402,F401  (XRAS card/page routes decorate bp)
from .xras import lifecycle_routes  # noqa: E402,F401  (XRAS lifecycle routes decorate bp)
from .xras import modals  # noqa: E402,F401  (XRAS read-detail modals decorate bp)
from . import xras_remediation_routes  # noqa: E402,F401  (XRAS write surface decorates bp)
