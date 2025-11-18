"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.
"""

from flask import Blueprint, render_template, jsonify, current_app
from flask_login import login_required, current_user
from webui.extensions import db
from webui.schemas import ProjectListSchema, AllocationWithUsageSchema
from sam.core.users import User
from sam.accounting.accounts import Account
from datetime import datetime

bp = Blueprint('user_dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@login_required
def index():
    """
    Main user dashboard.

    Shows user's projects and their allocation spending.
    """
    return render_template('user/dashboard.html', user=current_user)


@bp.route('/api/my-projects')
@login_required
def get_my_projects():
    """
    API endpoint to get current user's projects with allocation details.

    Returns:
        JSON with user's projects including allocation usage
    """
    # Get the SAM user object
    sam_user = db.session.query(User).filter_by(user_id=current_user.user_id).first()

    if not sam_user:
        return jsonify({'error': 'User not found'}), 404

    # Get user's active projects
    projects = sam_user.active_projects

    # Build response with project details and allocations
    projects_data = []

    for project in projects:
        # Get allocation usage for this project
        usage_data = project.get_detailed_allocation_usage(include_adjustments=True)

        # Calculate totals across all resources
        total_allocated = 0.0
        total_used = 0.0
        total_remaining = 0.0

        resources_list = []
        for resource_name, details in usage_data.items():
            total_allocated += details.get('allocated', 0.0)
            total_used += details.get('used', 0.0)
            total_remaining += details.get('remaining', 0.0)

            # Get adjustments (already a float from the method)
            adjustments_total = details.get('adjustments', 0.0)

            # Determine status based on end_date
            status = 'Active'
            end_date = details.get('end_date')
            if end_date and end_date < datetime.now():
                status = 'Expired'

            # Format dates for JSON serialization
            start_date = details.get('start_date')
            start_date_str = start_date.isoformat() if start_date else None
            end_date_str = end_date.isoformat() if end_date else None

            resources_list.append({
                'resource_name': resource_name,
                'allocated': details.get('allocated', 0.0),
                'used': details.get('used', 0.0),
                'remaining': details.get('remaining', 0.0),
                'percent_used': details.get('percent_used', 0.0),
                'charges_by_type': details.get('charges_by_type', {}),
                'adjustments': adjustments_total,
                'status': status,
                'start_date': start_date_str,
                'end_date': end_date_str,
            })

        # Calculate overall percent used
        percent_used = (total_used / total_allocated * 100.0) if total_allocated > 0 else 0.0

        projects_data.append({
            'projcode': project.projcode,
            'title': project.title,
            'active': project.active,
            'lead_username': project.lead.username if project.lead else None,
            'lead_name': project.lead.full_name if project.lead else None,
            'total_allocated': total_allocated,
            'total_used': total_used,
            'total_remaining': total_remaining,
            'percent_used': percent_used,
            'resources': resources_list,
        })

    return jsonify({
        'username': current_user.username,
        'projects': projects_data,
        'total_projects': len(projects_data)
    })


@bp.route('/api/project/<projcode>/details')
@login_required
def get_project_details(projcode):
    """
    API endpoint to get detailed allocation information for a specific project.

    Only accessible if user is a member of the project.

    Args:
        projcode: Project code

    Returns:
        JSON with detailed allocation and charge information
    """
    from sam.queries import find_project_by_code

    # Get the project
    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check if user is a member of this project
    sam_user = db.session.query(User).filter_by(user_id=current_user.user_id).first()
    user_projects = [p.projcode for p in sam_user.active_projects]

    if projcode not in user_projects:
        return jsonify({'error': 'Access denied - you are not a member of this project'}), 403

    # Get detailed usage
    usage_data = project.get_detailed_allocation_usage(include_adjustments=True)

    # Get accounts and allocations
    accounts = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.deleted == False
    ).all()

    allocations_list = []
    now = datetime.now()

    for account in accounts:
        if not account.resource:
            continue

        resource_name = account.resource.resource_name

        # Find active allocation
        for alloc in account.allocations:
            if alloc.is_active_at(now) and not alloc.deleted:
                # Get usage details from the usage_data dict
                resource_usage = usage_data.get(resource_name, {})

                allocations_list.append({
                    'allocation_id': alloc.allocation_id,
                    'resource_name': resource_name,
                    'resource_type': account.resource.resource_type.resource_type if account.resource.resource_type else 'UNKNOWN',
                    'amount': float(alloc.amount) if alloc.amount else 0.0,
                    'used': resource_usage.get('used', 0.0),
                    'remaining': resource_usage.get('remaining', 0.0),
                    'percent_used': resource_usage.get('percent_used', 0.0),
                    'charges_by_type': resource_usage.get('charges_by_type', {}),
                    'start_date': alloc.start_date.isoformat() if alloc.start_date else None,
                    'end_date': alloc.end_date.isoformat() if alloc.end_date else None,
                })

    return jsonify({
        'projcode': project.projcode,
        'title': project.title,
        'lead': {
            'username': project.lead.username if project.lead else None,
            'name': project.lead.full_name if project.lead else None,
        },
        'allocations': allocations_list,
        'total_allocations': len(allocations_list)
    })


@bp.route('/api/project/<projcode>/tree')
@login_required
def get_project_tree(projcode):
    """
    Get project hierarchy (parent and children).

    Args:
        projcode: Project code

    Returns:
        JSON with parent project and list of child projects
    """
    from sam.projects.projects import Project

    # Get the SAM user object
    sam_user = db.session.query(User).filter_by(user_id=current_user.user_id).first()
    if not sam_user:
        return jsonify({'error': 'User not found'}), 404

    # Verify user has access to at least one project
    user_projects = [p.projcode for p in sam_user.all_projects]

    # Get project
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check if user has access to this project OR any of its parents/children
    # (Allow viewing tree if user is member of any related project)
    has_access = projcode in user_projects
    if not has_access:
        # Check if user has access to parent
        if project.parent and project.parent.projcode in user_projects:
            has_access = True
        # Check if user has access to any children
        if not has_access:
            for child in project.get_children():
                if child.projcode in user_projects:
                    has_access = True
                    break

    if not has_access:
        return jsonify({'error': 'Access denied'}), 403

    # Helper function to get detailed resource usage for a project
    def get_project_resource_usage(proj):
        usage = proj.get_detailed_allocation_usage(include_adjustments=True)

        resources = []
        total_allocated = 0
        total_used = 0

        for resource_name, details in usage.items():
            allocated = details.get('allocated', 0)
            used = details.get('used', 0)
            remaining = details.get('remaining', 0)
            percent_used = details.get('percent_used', 0)

            total_allocated += allocated
            total_used += used

            resources.append({
                'resource_name': resource_name,
                'allocated': allocated,
                'used': used,
                'remaining': remaining,
                'percent_used': percent_used
            })

        overall_percent = (total_used / total_allocated * 100) if total_allocated > 0 else 0

        return {
            'resources': resources,
            'total_allocated': total_allocated,
            'total_used': total_used,
            'total_remaining': total_allocated - total_used,
            'percent_used': overall_percent
        }

    # Get parent with resource details
    parent_data = None
    if project.parent:
        parent_usage = get_project_resource_usage(project.parent)
        parent_data = {
            'projcode': project.parent.projcode,
            'title': project.parent.title,
            'active': project.parent.active,
            'usage': parent_usage
        }

    # Get current project resource usage
    current_usage = get_project_resource_usage(project)

    # Get children with resource details
    children_data = []
    children = project.get_children()
    for child in children:
        child_usage = get_project_resource_usage(child)
        children_data.append({
            'projcode': child.projcode,
            'title': child.title,
            'active': child.active,
            'has_children': child.has_children,
            'usage': child_usage
        })

    return jsonify({
        'projcode': project.projcode,
        'title': project.title,
        'parent': parent_data,
        'children': children_data,
        'child_count': len(children_data),
        'usage': current_usage
    })


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
    """
    from flask import request
    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        from flask import flash, redirect, url_for
        flash('Missing project code or resource name', 'error')
        return redirect(url_for('user_dashboard.index'))

    return render_template('user/resource_details.html',
                          user=current_user,
                          projcode=projcode,
                          resource_name=resource_name)


@bp.route('/api/resource-usage-timeseries')
@login_required
def get_resource_usage_timeseries():
    """
    API endpoint to get time series data for resource usage.

    Query parameters:
        projcode: Project code
        resource: Resource name
        start_date: Start date (YYYY-MM-DD) - optional, defaults to 90 days ago
        end_date: End date (YYYY-MM-DD) - optional, defaults to today

    Returns:
        JSON with daily usage data for charting
    """
    from flask import request
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sam.summaries.comp_summaries import CompChargeSummary
    from sam.summaries.dav_summaries import DavChargeSummary
    from sam.summaries.disk_summaries import DiskChargeSummary
    from sam.summaries.archive_summaries import ArchiveChargeSummary
    from sqlalchemy import func
    from datetime import timedelta

    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        return jsonify({'error': 'Missing required parameters'}), 400

    # Get project
    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check user access
    sam_user = db.session.query(User).filter_by(user_id=current_user.user_id).first()
    user_projects = [p.projcode for p in sam_user.active_projects]
    if projcode not in user_projects:
        return jsonify({'error': 'Access denied'}), 403

    # Get resource
    resource = db.session.query(Resource).filter_by(resource_name=resource_name).first()
    if not resource:
        return jsonify({'error': 'Resource not found'}), 404

    # Parse dates
    try:
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d') if request.args.get('end_date') else datetime.now()
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d') if request.args.get('start_date') else end_date - timedelta(days=90)
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400

    # Get account for this project/resource
    account = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.resource_id == resource.resource_id,
        Account.deleted == False
    ).first()

    if not account:
        return jsonify({'error': 'No account found for this project/resource'}), 404

    # Determine resource type and query appropriate summary tables
    resource_type = resource.resource_type.resource_type if resource.resource_type else 'UNKNOWN'

    daily_data = {}

    if resource_type in ['HPC', 'DAV']:
        # Query comp charges
        comp_data = db.session.query(
            CompChargeSummary.activity_date,
            func.sum(CompChargeSummary.charges).label('total_charges')
        ).filter(
            CompChargeSummary.account_id == account.account_id,
            CompChargeSummary.activity_date >= start_date,
            CompChargeSummary.activity_date <= end_date
        ).group_by(CompChargeSummary.activity_date).all()

        for date, charges in comp_data:
            date_str = date.strftime('%Y-%m-%d')
            if date_str not in daily_data:
                daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_data[date_str]['comp'] = float(charges or 0.0)

        # Query dav charges
        dav_data = db.session.query(
            DavChargeSummary.activity_date,
            func.sum(DavChargeSummary.charges).label('total_charges')
        ).filter(
            DavChargeSummary.account_id == account.account_id,
            DavChargeSummary.activity_date >= start_date,
            DavChargeSummary.activity_date <= end_date
        ).group_by(DavChargeSummary.activity_date).all()

        for date, charges in dav_data:
            date_str = date.strftime('%Y-%m-%d')
            if date_str not in daily_data:
                daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_data[date_str]['dav'] = float(charges or 0.0)

    elif resource_type == 'DISK':
        disk_data = db.session.query(
            DiskChargeSummary.activity_date,
            func.sum(DiskChargeSummary.charges).label('total_charges')
        ).filter(
            DiskChargeSummary.account_id == account.account_id,
            DiskChargeSummary.activity_date >= start_date,
            DiskChargeSummary.activity_date <= end_date
        ).group_by(DiskChargeSummary.activity_date).all()

        for date, charges in disk_data:
            date_str = date.strftime('%Y-%m-%d')
            daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': float(charges or 0.0), 'archive': 0.0}

    elif resource_type == 'ARCHIVE':
        archive_data = db.session.query(
            ArchiveChargeSummary.activity_date,
            func.sum(ArchiveChargeSummary.charges).label('total_charges')
        ).filter(
            ArchiveChargeSummary.account_id == account.account_id,
            ArchiveChargeSummary.activity_date >= start_date,
            ArchiveChargeSummary.activity_date <= end_date
        ).group_by(ArchiveChargeSummary.activity_date).all()

        for date, charges in archive_data:
            date_str = date.strftime('%Y-%m-%d')
            daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': float(charges or 0.0)}

    # Convert to sorted list for charting
    sorted_data = sorted([
        {'date': date, **values}
        for date, values in daily_data.items()
    ], key=lambda x: x['date'])

    return jsonify({
        'projcode': projcode,
        'resource_name': resource_name,
        'resource_type': resource_type,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'data': sorted_data
    })


@bp.route('/api/resource-jobs')
@login_required
def get_resource_jobs():
    """
    API endpoint to get job details for a specific resource.

    Query parameters:
        projcode: Project code
        resource: Resource name
        start_date: Start date (YYYY-MM-DD) - optional, defaults to 30 days ago
        end_date: End date (YYYY-MM-DD) - optional, defaults to today
        limit: Number of jobs to return (default: 100, max: 1000)

    Returns:
        JSON with list of jobs including job ID, date/time, and resource usage
    """
    from flask import request
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sam.activity.computational import CompJob
    from datetime import timedelta

    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')
    limit = min(int(request.args.get('limit', 100)), 1000)

    if not projcode or not resource_name:
        return jsonify({'error': 'Missing required parameters'}), 400

    # Get project
    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check user access
    sam_user = db.session.query(User).filter_by(user_id=current_user.user_id).first()
    user_projects = [p.projcode for p in sam_user.active_projects]
    if projcode not in user_projects:
        return jsonify({'error': 'Access denied'}), 403

    # Parse dates
    try:
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d') if request.args.get('end_date') else datetime.now()
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d') if request.args.get('start_date') else end_date - timedelta(days=30)
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400

    # Query jobs for this project and resource
    jobs = db.session.query(CompJob).filter(
        CompJob.projcode == projcode,
        CompJob.resource == resource_name,
        CompJob.activity_date >= start_date,
        CompJob.activity_date <= end_date
    ).order_by(CompJob.activity_date.desc()).limit(limit).all()

    # Format job data
    jobs_data = []
    for job in jobs:
        jobs_data.append({
            'job_id': job.job_id,
            'job_idx': job.job_idx,
            'username': job.username,
            'machine': job.machine,
            'queue': job.queue,
            'job_name': job.job_name or 'N/A',
            'activity_date': job.activity_date.isoformat() if job.activity_date else None,
            'submit_time': datetime.fromtimestamp(job.submit_time).isoformat() if job.submit_time else None,
            'start_time': datetime.fromtimestamp(job.start_time).isoformat() if job.start_time else None,
            'end_time': datetime.fromtimestamp(job.end_time).isoformat() if job.end_time else None,
            'wall_time_hours': job.wall_time_hours,
            'queue_wait_hours': job.queue_wait_time_seconds / 3600.0 if job.queue_wait_time_seconds else 0.0,
            'exit_status': job.exit_status,
            'is_successful': job.is_successful,
            'interactive': bool(job.interactive),
        })

    return jsonify({
        'projcode': projcode,
        'resource_name': resource_name,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'total_jobs': len(jobs_data),
        'jobs': jobs_data
    })
