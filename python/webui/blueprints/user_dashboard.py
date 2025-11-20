"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime, timedelta

from webui.extensions import db
from sam.queries import get_user_dashboard_data, get_resource_detail_data, get_users_on_project
from webui.utils.charts import generate_usage_timeseries_matplotlib

bp = Blueprint('user_dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@login_required
def index():
    """
    Main user dashboard.

    Shows user's projects and their allocation spending.
    Data is loaded server-side using direct ORM queries for improved performance.
    """
    # Fetch all dashboard data using optimized query helper
    dashboard_data = get_user_dashboard_data(db.session, current_user.user_id)

    return render_template(
        'user/dashboard.html',
        user=current_user,
        dashboard_data=dashboard_data
    )


@bp.route('/resource-details')
@login_required
def resource_details():
    """
    Resource usage detail view showing charts and job history.

    Query parameters:
        projcode: Project code
        resource: Resource name
        start_date: Optional start date (default: 30 days ago)
        end_date: Optional end date (default: today)

    Returns:
        HTML page with server-rendered charts and usage data
    """
    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        flash('Missing project code or resource name', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Parse date range (default to last 30 days)
    try:
        if request.args.get('start_date'):
            start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d')
        else:
            start_date = datetime.now() - timedelta(days=30)

        if request.args.get('end_date'):
            end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d')
        else:
            end_date = datetime.now()
    except ValueError:
        flash('Invalid date format. Please use YYYY-MM-DD.', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Fetch resource detail data
    detail_data = get_resource_detail_data(
        db.session,
        projcode,
        resource_name,
        start_date,
        end_date
    )

    if not detail_data:
        flash(f'Project {projcode} or resource {resource_name} not found', 'error')
        return redirect(url_for('user_dashboard.index'))

    print(detail_data)

    # Generate charts server-side
    usage_chart = generate_usage_timeseries_matplotlib(detail_data['daily_charges'])
    #breakdown_chart = generate_charge_breakdown_bars(detail_data['charge_totals'])

    return render_template(
        'user/resource_details.html',
        user=current_user,
        projcode=projcode,
        resource_name=resource_name,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        detail_data=detail_data,
        usage_chart=usage_chart,
    )


@bp.route('/members/<projcode>')
@login_required
def members_fragment(projcode):
    """
    Lazy-loaded HTML fragment showing project members.

    Returns:
        HTML table of project members (no full page layout)
    """
    members = get_users_on_project(db.session, projcode)

    if not members:
        return '<p class="text-muted mb-0">No members found or project not accessible</p>'

    return render_template(
        'user/fragments/members_table.html',
        members=sorted(members, key=lambda member: member["display_name"])
    )


@bp.route('/tree/<projcode>')
@login_required
def tree_fragment(projcode):
    """
    Lazy-loaded HTML fragment showing project hierarchy tree.

    Returns:
        HTML tree structure (no full page layout)
    """
    from sam.queries import find_project_by_code

    project = find_project_by_code(db.session, projcode)

    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    # Get the root of the project tree
    root = project.get_root() if hasattr(project, 'get_root') else project

    # Render tree structure recursively
    def render_tree_node(node, current_projcode, level=0):
        is_current = node.projcode == current_projcode
        style = 'background: #fff3cd; font-weight: bold; border-left-color: #ffc107;' if is_current else ''
        icon = '<i class="fas fa-arrow-right text-warning mr-1"></i>' if is_current else ''

        html = f'<li style="{style}">{icon}<strong>{node.projcode}</strong>'

        if node.title:
            html += f' - {node.title}'

        # Recursively render children
        children = node.children if hasattr(node, 'children') and node.children else []
        if children:
            html += '<ul class="tree-list">'
            for child in children:
                html += render_tree_node(child, current_projcode, level + 1)
            html += '</ul>'

        html += '</li>'
        return html

    tree_html = f'<ul class="tree-list">{render_tree_node(root, projcode)}</ul>'

    return tree_html
