"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

NOTE: All API functionality has been consolidated to /api/v1/ endpoints.
This blueprint now only handles HTML page rendering.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

bp = Blueprint('user_dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@login_required
def index():
    """
    Main user dashboard.

    Shows user's projects and their allocation spending.
    All data is loaded via JavaScript from API v1 endpoints:
    - GET /api/v1/users/me/projects?format=dashboard
    - GET /api/v1/projects/<projcode>
    - GET /api/v1/projects/<projcode>/allocations
    """
    return render_template('user/dashboard.html', user=current_user)


@bp.route('/resource-details')
@login_required
def resource_details():
    """
    Resource usage detail view showing charts and job history.

    Query parameters:
        projcode: Project code
        resource: Resource name

    Returns:
        HTML page with usage charts and job details

    Data is loaded via JavaScript from API v1 endpoints:
    - GET /api/v1/projects/<projcode>/jobs
    - GET /api/v1/projects/<projcode>/charges
    - GET /api/v1/charges/details
    """
    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        flash('Missing project code or resource name', 'error')
        return redirect(url_for('user_dashboard.index'))

    return render_template('user/resource_details.html',
                          user=current_user,
                          projcode=projcode,
                          resource_name=resource_name)
