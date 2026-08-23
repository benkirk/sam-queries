"""
Allocations dashboard blueprint.

Provides admin/staff dashboard for viewing allocation summaries across
all projects, resources, and facilities.
"""

from flask import Blueprint

bp = Blueprint('allocations_dashboard', __name__, url_prefix='/allocations')

__all__ = ['bp']

from . import blueprint  # noqa: E402,F401  (non-XRAS routes decorate bp)
from . import xras        # noqa: E402,F401  (xras/__init__ imports the XRAS route modules)
