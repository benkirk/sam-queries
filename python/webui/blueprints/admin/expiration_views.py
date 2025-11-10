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
from sam.queries import get_projects_by_allocation_end_date, get_projects_with_expired_allocations


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
        '30days': 30,
        '60days': 60
    }

    EXPIRED_PRESETS = {
        '30days': 30,
        '90days': 90,
        '180days': 180
    }

    def __init__(self, *args, **kwargs):
        """Initialize the view."""
        super().__init__(*args, **kwargs)
        self._default_facilities = ['UNIV', 'WNA']

    def _get_session(self) -> Session:
        """Get SQLAlchemy session from Flask app."""
        return current_app.Session()

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
        custom_days = request.args.get('custom_days', None)

        return {
            'facilities': facilities,
            'resource': resource,
            'time_range': time_range,
            'custom_days': custom_days
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

    def _get_expired_projects(self, session: Session, days: int,
                              facilities: List[str], resource: Optional[str] = None):
        """
        Query recently expired projects.

        Args:
            session: SQLAlchemy session
            days: Maximum days since expiration
            facilities: List of facility names to filter
            resource: Optional resource name to filter

        Returns:
            List of (Project, Allocation, resource_name, days_expired) tuples
        """
        return get_projects_with_expired_allocations(
            session,
            max_days_expired=days,
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
            if not user.active:
                continue

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

        # Default to 30 days for both views
        days = 30
        if filters['time_range']:
            if active_tab == 'upcoming' and filters['time_range'] in self.UPCOMING_PRESETS:
                days = self.UPCOMING_PRESETS[filters['time_range']]
            elif active_tab == 'expired' and filters['time_range'] in self.EXPIRED_PRESETS:
                days = self.EXPIRED_PRESETS[filters['time_range']]
        elif filters['custom_days']:
            try:
                days = int(filters['custom_days'])
            except (ValueError, TypeError):
                days = 30

        # Query based on active tab
        data = []
        abandoned_users = []

        if active_tab == 'upcoming':
            results = self._get_upcoming_expirations(
                session, days, filters['facilities'], filters['resource']
            )
            data = self._format_results_for_template(results, 'upcoming')

        elif active_tab == 'expired':
            results = self._get_expired_projects(
                session, days, filters['facilities'], filters['resource']
            )
            data = self._format_results_for_template(results, 'expired')

        elif active_tab == 'abandoned':
            # For abandoned users, we need expired projects
            # Use 90 days as default
            expired_days = 90
            results = self._get_expired_projects(
                session, expired_days, filters['facilities'], filters['resource']
            )
            abandoned_users_list = self._get_abandoned_users(session, results)
            abandoned_users = self._format_abandoned_users_for_template(abandoned_users_list)

        session.close()

        return self.render(
            'admin/expirations_dashboard.html',
            active_tab=active_tab,
            data=data,
            abandoned_users=abandoned_users,
            filters=filters,
            all_facilities=all_facilities,
            upcoming_presets=self.UPCOMING_PRESETS,
            expired_presets=self.EXPIRED_PRESETS,
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

        # Determine days based on filters
        days = 30
        if filters['time_range']:
            if export_type == 'upcoming' and filters['time_range'] in self.UPCOMING_PRESETS:
                days = self.UPCOMING_PRESETS[filters['time_range']]
            elif export_type == 'expired' and filters['time_range'] in self.EXPIRED_PRESETS:
                days = self.EXPIRED_PRESETS[filters['time_range']]
        elif filters['custom_days']:
            try:
                days = int(filters['custom_days'])
            except (ValueError, TypeError):
                days = 30

        # Create CSV in memory
        output = io.StringIO()

        if export_type == 'abandoned':
            # Export abandoned users
            expired_results = self._get_expired_projects(
                session, 90, filters['facilities'], filters['resource']
            )
            users = self._get_abandoned_users(session, expired_results)

            writer = csv.writer(output)
            writer.writerow(['Username', 'Display Name', 'Email', 'Active Projects'])

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
                results = self._get_expired_projects(
                    session, days, filters['facilities'], filters['resource']
                )
                days_label = 'Days Since Expiration'

            writer = csv.writer(output)
            writer.writerow([
                'Project Code', 'Title', 'Lead Name', 'Lead Username',
                'Resource', 'End Date', days_label, 'Active'
            ])

            for proj, alloc, res_name, days_val in results:
                writer.writerow([
                    proj.projcode,
                    proj.title,
                    proj.lead.display_name if proj.lead else 'N/A',
                    proj.lead.username if proj.lead else 'N/A',
                    res_name,
                    alloc.end_date.strftime('%Y-%m-%d') if alloc.end_date else 'N/A',
                    days_val,
                    'Yes' if proj.active else 'No'
                ])

            filename = f'{export_type}_projects_{datetime.now().strftime("%Y%m%d")}.csv'

        session.close()

        # Create response
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
