"""Email notification service for SAM CLI."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Tuple, Optional, List, Dict
from jinja2 import Environment, FileSystemLoader
import jinja2


class EmailNotificationService:
    """Service for sending email notifications."""

    def __init__(self, ctx):
        """Initialize email service with context.

        Args:
            ctx: Context object with email configuration
        """
        self.ctx = ctx
        self.mail_server = ctx.mail_server
        self.mail_port = ctx.mail_port
        self.mail_use_tls = ctx.mail_use_tls
        self.mail_username = ctx.mail_username
        self.mail_password = ctx.mail_password
        self.mail_from = ctx.mail_from

        # Setup Jinja2 template environment
        template_dir = Path(__file__).parent.parent / 'templates'
        self.jinja_env = Environment(loader=FileSystemLoader(str(template_dir)))

    def _get_template_name(self, base_name: str, facility: str = None, extension: str = 'txt') -> str:
        """
        Get template name with facility-specific fallback.

        Resolution order:
        1. {base_name}-{facility}.{extension} (e.g., expiration-UNIV.txt)
        2. {base_name}.{extension} (e.g., expiration.txt)

        Args:
            base_name: Base template name (e.g., 'expiration')
            facility: Facility name (e.g., 'UNIV', 'WNA', 'NCAR')
            extension: File extension (e.g., 'txt', 'html')

        Returns:
            Template filename to use
        """
        if facility:
            facility_template = f"{base_name}-{facility}.{extension}"
            try:
                # Check if facility-specific template exists
                self.jinja_env.get_template(facility_template)
                return facility_template
            except jinja2.exceptions.TemplateNotFound:
                pass  # Fall back to generic

        return f"{base_name}.{extension}"

    def send_expiration_notification(
        self,
        recipient: str,
        project_code: str,
        project_title: str,
        resources: List[Dict],
        recipient_name: str,
        recipient_role: str = 'user',
        project_lead: str = None,
        grace_expiration: str = None,
        facility: str = None
    ) -> Tuple[bool, Optional[str]]:
        """Send expiration notification email.

        Args:
            recipient: Email address of recipient
            project_code: Project code (e.g., 'SCSG0001')
            project_title: Project title
            resources: List of resource dicts with keys:
                - resource_name: Name of resource (e.g., 'Derecho')
                - expiration_date: Date string (e.g., '2025-01-15')
                - days_remaining: Number of days until expiration
                - allocated_amount: Allocated amount
                - used_amount: Used amount
                - remaining_amount: Remaining amount
                - units: Units string (e.g., 'core-hours')
            recipient_name: Recipient's display name
            recipient_role: Recipient's role ('lead', 'admin', or 'user')
            project_lead: Name of project lead (for user emails)
            grace_expiration: Grace period end date (YYYY-MM-DD format)
            facility: Facility name for template selection (e.g., 'UNIV', 'WNA')

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            # Select templates with facility-specific fallback
            text_template_name = self._get_template_name('expiration', facility, 'txt')
            html_template_name = self._get_template_name('expiration', facility, 'html')

            # Load text template (required)
            text_template = self.jinja_env.get_template(text_template_name)

            # Try to load HTML template (optional)
            html_template = None
            try:
                html_template = self.jinja_env.get_template(html_template_name)
            except jinja2.exceptions.TemplateNotFound:
                # HTML template doesn't exist, will send text-only
                pass

            # Prepare template variables
            template_vars = {
                'recipient_name': recipient_name,
                'project_code': project_code,
                'project_title': project_title,
                'resources': resources,
                'recipient_role': recipient_role,
                'project_lead': project_lead,
                'grace_expiration': grace_expiration
            }

            # Render text content
            text_content = text_template.render(**template_vars)

            # Create message (multipart if HTML exists, plain text otherwise)
            if html_template:
                # Multipart message with both text and HTML
                msg = MIMEMultipart('alternative')
                msg['Subject'] = f'SAM Allocation Expiration Notice - {project_code}'
                msg['From'] = self.mail_from
                msg['To'] = recipient

                html_content = html_template.render(**template_vars)
                msg.attach(MIMEText(text_content, 'plain'))
                msg.attach(MIMEText(html_content, 'html'))
            else:
                # Plain text only
                msg = MIMEText(text_content, 'plain')
                msg['Subject'] = f'SAM Allocation Expiration Notice - {project_code}'
                msg['From'] = self.mail_from
                msg['To'] = recipient

            # Send email
            with smtplib.SMTP(self.mail_server, self.mail_port) as smtp:
                if self.mail_use_tls:
                    smtp.starttls()
                if self.mail_username and self.mail_password:
                    smtp.login(self.mail_username, self.mail_password)
                smtp.send_message(msg)

            return (True, None)

        except Exception as e:
            error_msg = f"Failed to send email to {recipient}: {str(e)}"
            return (False, error_msg)

    def send_batch_notifications(self, notifications: List[Dict]) -> Dict:
        """Send multiple notifications in batch.

        Args:
            notifications: List of notification dicts with keys:
                - recipient: Email address
                - project_code: Project code
                - project_title: Project title
                - resources: List of resource dicts
                - recipient_name: Recipient's display name
                - recipient_role: Recipient's role (optional)
                - project_lead: Project lead name (optional)
                - grace_expiration: Grace expiration date (optional)
                - facility: Facility name (optional)

        Returns:
            Dict with 'success' and 'failed' lists containing notification dicts
        """
        results = {
            'success': [],
            'failed': []
        }

        for notification in notifications:
            success, error = self.send_expiration_notification(
                recipient=notification['recipient'],
                project_code=notification['project_code'],
                project_title=notification['project_title'],
                resources=notification['resources'],
                recipient_name=notification['recipient_name'],
                recipient_role=notification.get('recipient_role', 'user'),
                project_lead=notification.get('project_lead'),
                grace_expiration=notification.get('grace_expiration'),
                facility=notification.get('facility')
            )

            if success:
                results['success'].append(notification)
            else:
                notification['error'] = error
                results['failed'].append(notification)

        return results
