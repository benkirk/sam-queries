"""Allocations > XRAS > Configuration: the request types as a PI sees them, editable.

Viewed with ``VIEW_XRAS`` (the XRAS tab's permission, not facility-scopable). Design:
``docs/plans/XRAS_SUBMISSION_SPRINT3.md``.
"""

from flask import render_template
from flask_login import login_required

from sam.xras.audiences import AUDIENCES
from webapp.utils.rbac import Permission, require_permission

from .. import bp


@bp.route('/xras/configuration')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_configuration():
    """The page: the "Preview as" switch over the lazy type cards."""
    return render_template('dashboards/allocations/xras_configuration.html',
                           audiences=AUDIENCES)
