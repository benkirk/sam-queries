"""
Flask-Admin custom view for project expiration monitoring.

This module provides a comprehensive dashboard for tracking:
- Upcoming project expirations
- Recently expired projects
- Abandoned users (users with only expired projects)
"""

from datetime import datetime, timedelta
from typing import List, Tuple, Optional
import csv
import io

from flask import request, Response, url_for, current_app
from flask_admin import BaseView, expose
from sqlalchemy.orm import Session
from tqdm import tqdm

from sam import Project, Allocation, User, Facility
from sam.queries.expirations import get_projects_by_allocation_end_date, get_projects_with_expired_allocations


class ProjectExpirationView(BaseView):
    """
    Custom Flask-Admin view for monitoring project expirations.

    Provides:
    - Dashboard with upcoming/expired/abandoned tabs
    - Filtering by facility, resource, and date range
    - CSV/Excel export functionality
    - Links to project and user detail pages
    """

    # Preset time ranges for quick access
    UPCOMING_PRESETS = {
        '7days': 7,
        '31days': 31,
        '60days': 60
    }

    def __init__(self, *args, **kwargs):
        """Initialize the view."""
        super().__init__(*args, **kwargs)
        self._default_facilities = ['UNIV', 'WNA']

    def _get_session(self) -> Session:
        """Get SQLAlchemy session from Flask-SQLAlchemy."""
        from webapp.extensions import db
        return db.session

    def _get_all_facilities(self, session: Session) -> List[Tuple[str, str]]:
        """
        Get all available facilities for filter dropdown.

        Returns:
            List of (facility_name, facility_name) tuples for form options
        """
        facilities = session.query(Facility.facility_name).distinct().order_by(Facility.facility_name).all()
        return [(f[0], f[0]) for f in facilities]

    def _parse_filter_params(self):
        """
        Parse filter parameters from request.

        Returns:
            dict with filter parameters
        """
        # Get selected facilities from form (multi-select)
        facilities = request.args.getlist('facilities')
        if not facilities:
            facilities = self._default_facilities

        # Get resource filter (single select, optional)
        resource = request.args.get('resource', None)
        if resource == '':
            resource = None

        # Get time range preset or custom days
        time_range = request.args.get('time_range', None)

        return {
            'facilities': facilities,
            'resource': resource,
            'time_range': time_range,
        }

    def _get_upcoming_expirations(self, session: Session, days: int,
                                   facilities: List[str], resource: Optional[str] = None):
        """
        Query upcoming project expirations.

        Args:
            session: SQLAlchemy session
            days: Number of days in future to check
            facilities: List of facility names to filter
            resource: Optional resource name to filter

        Returns:
            List of (Project, Allocation, resource_name, days_remaining) tuples
        """
        return get_projects_by_allocation_end_date(
            session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=days),
            facility_names=facilities if facilities else None,
            resource_name=resource
        )

    def _get_expired_projects(self, session: Session,
                              facilities: List[str], resource: Optional[str] = None):
        """
        Query recently expired projects.

        Shows projects expired between 90-365 days ago.

        Args:
            session: SQLAlchemy session
            facilities: List of facility names to filter
            resource: Optional resource name to filter

        Returns:
            List of (Project, Allocation, resource_name, days_expired) tuples
        """
        return get_projects_with_expired_allocations(
            session,
            max_days_expired=365,
            min_days_expired=0,
            facility_names=facilities if facilities else None,
            resource_name=resource
        )

    def _get_abandoned_users(self, session: Session, expired_projects: List[Tuple]):
        """
        Find users who only have expired projects.

        Args:
            session: SQLAlchemy session
            expired_projects: List of (Project, Allocation, resource_name, days) tuples

        Returns:
            List of User objects who are "abandoned" (only have expired projects)
        """
        all_users = set()
        expired_projcodes = set()

        # Collect all users from expired projects
        for proj, alloc, res_name, days in expired_projects:
            all_users.update(proj.roster)
            expired_projcodes.add(proj.projcode)

        # Find users whose active projects are all in the expired set
        abandoned_users = []
        for user in all_users:
            #if not user.active:
            #    continue

            user_active_projcodes = set(p.projcode for p in user.active_projects)

            # If user has active projects and they're ALL in the expired set, user is abandoned
            if user_active_projcodes and user_active_projcodes.issubset(expired_projcodes):
                abandoned_users.append(user)

        return sorted(abandoned_users, key=lambda u: u.username)

    def _format_results_for_template(self, results: List[Tuple], view_type: str):
        """
        Format query results for template display.

        Args:
            results: List of (Project, Allocation, resource_name, days) tuples
            view_type: 'upcoming' or 'expired' for appropriate label

        Returns:
            List of dicts with formatted data and links
        """
        formatted = []
        for proj, alloc, res_name, days in results:
            # Generate URLs for linking
            project_url = url_for('projects.edit_view', id=proj.project_id)
            lead_url = url_for('users.edit_view', id=proj.lead.user_id) if proj.lead else None

            formatted.append({
                'projcode': proj.projcode,
                'project_url': project_url,
                'title': proj.title,
                'lead_name': proj.lead.display_name if proj.lead else 'N/A',
                'lead_username': proj.lead.username if proj.lead else 'N/A',
                'lead_url': lead_url,
                'resource_name': res_name,
                'end_date': alloc.end_date.strftime('%Y-%m-%d') if alloc.end_date else 'N/A',
                'days': days,
                'days_label': f"{days} days" if view_type == 'upcoming' else f"{days} days ago",
                'active': proj.active
            })
        return formatted

    def _format_abandoned_users_for_template(self, users: List[User]):
        """
        Format abandoned users for template display.

        Args:
            users: List of User objects

        Returns:
            List of dicts with formatted user data and links
        """
        formatted = []
        for user in users:
            user_url = url_for('users.edit_view', id=user.user_id)

            # Get user's project codes
            project_codes = [p.projcode for p in user.active_projects]

            formatted.append({
                'username': user.username,
                'user_url': user_url,
                'display_name': user.display_name,
                'email': user.primary_email or 'N/A',
                'project_count': len(project_codes),
                'projects': ', '.join(sorted(project_codes))
            })
        return formatted

    @expose('/')
    def index(self):
        """
        Main expiration dashboard with tabs.

        Shows upcoming expirations by default, with tabs for expired and abandoned.
        """
        session = self._get_session()
        filters = self._parse_filter_params()

        # Determine which tab to show
        active_tab = request.args.get('tab', 'upcoming')

        # Get available facilities for filter
        all_facilities = self._get_all_facilities(session)

        # Process time_range filter only for upcoming tab
        # Default to 31 days for upcoming expirations
        days = 31
        if active_tab == 'upcoming' and filters['time_range']:
            if filters['time_range'] in self.UPCOMING_PRESETS:
                days = self.UPCOMING_PRESETS[filters['time_range']]

        # Query based on active tab
        data = []
        abandoned_users = []

        if active_tab == 'upcoming':
            results = self._get_upcoming_expirations(
                session, days, filters['facilities'], filters['resource']
            )
            data = self._format_results_for_template(results, 'upcoming')

        elif active_tab == 'expired':
            # Expired projects use hardcoded 90-365 day window
            results = self._get_expired_projects(
                session, filters['facilities'], filters['resource']
            )
            data = self._format_results_for_template(results, 'expired')

        elif active_tab == 'abandoned':
            # Abandoned users are based on expired projects (90-365 day window)
            results = self._get_expired_projects(
                session, filters['facilities'], filters['resource']
            )
            abandoned_users_list = self._get_abandoned_users(session, results)
            abandoned_users = self._format_abandoned_users_for_template(abandoned_users_list)

        # Flask-SQLAlchemy handles session cleanup automatically via teardown_appcontext

        return self.render(
            'admin/expirations_dashboard.html',
            active_tab=active_tab,
            data=data,
            abandoned_users=abandoned_users,
            filters=filters,
            all_facilities=all_facilities,
            upcoming_presets=self.UPCOMING_PRESETS,
            current_days=days
        )

    @expose('/export')
    def export(self):
        """
        Export expiration data to CSV.

        Supports exporting upcoming, expired, or abandoned user data.
        """
        session = self._get_session()
        filters = self._parse_filter_params()
        export_type = request.args.get('export_type', 'upcoming')

        # Process time_range filter only for upcoming exports
        # Default to 31 days for upcoming expirations
        days = 31
        if export_type == 'upcoming' and filters['time_range']:
            if filters['time_range'] in self.UPCOMING_PRESETS:
                days = self.UPCOMING_PRESETS[filters['time_range']]

        # Create CSV in memory
        output = io.StringIO()

        if export_type == 'abandoned':
            # Export abandoned users (based on expired projects with 90-365 day window)
            expired_results = self._get_expired_projects(
                session, filters['facilities'], filters['resource']
            )
            users = self._get_abandoned_users(session, expired_results)

            writer = csv.writer(output)
            writer.writerow(['Username', 'Display Name', 'Email', 'Expiring Projects'])

            for user in users:
                project_codes = ', '.join(p.projcode for p in user.active_projects)
                writer.writerow([
                    user.username,
                    user.display_name,
                    user.primary_email or 'N/A',
                    project_codes
                ])

            filename = f'abandoned_users_{datetime.now().strftime("%Y%m%d")}.csv'

        else:
            # Export projects (upcoming or expired)
            if export_type == 'upcoming':
                results = self._get_upcoming_expirations(
                    session, days, filters['facilities'], filters['resource']
                )
                days_label = 'Days Remaining'
            else:
                # Expired exports use hardcoded 90-365 day window
                results = self._get_expired_projects(
                    session, filters['facilities'], filters['resource']
                )
                days_label = 'Days Since Expiration'

            writer = csv.writer(output)
            writer.writerow([
                'Project Code', 'Title', 'Lead Name', 'Lead Username', 'End Date', days_label
            ])

            for proj, alloc, res_name, days_val in results:
                writer.writerow([
                    proj.projcode,
                    proj.title,
                    proj.lead.display_name if proj.lead else 'N/A',
                    proj.lead.username if proj.lead else 'N/A',
                    alloc.end_date.strftime('%Y-%m-%d') if alloc.end_date else 'N/A',
                    days_val
                ])

            filename = f'{export_type}_projects_{datetime.now().strftime("%Y%m%d")}.csv'

        # Flask-SQLAlchemy handles session cleanup automatically via teardown_appcontext

        # Create response
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
