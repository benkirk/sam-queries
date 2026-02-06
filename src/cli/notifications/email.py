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

    def send_expiration_notification(self, notification: Dict) -> Tuple[bool, Optional[str]]:
        """Send expiration notification email.

        Args:
            notification: Notification dict with fields:
                - subject: Email Subject
                - recipient: Email address of recipient
                - recipient_name: Recipient's display name
                - recipient_role: Recipient's role ('lead', 'admin', or 'user')
                - project_code: Project code (e.g., 'SCSG0001')
                - project_title: Project title
                - project_lead: Name of project lead (for user emails)
                - project_lead_email: Email of project lead
                - resources: List of resource dicts with keys:
                    - resource_name: Name of resource (e.g., 'Derecho')
                    - expiration_date: Date string (e.g., '2025-01-15')
                    - days_remaining: Number of days until expiration
                    - allocated_amount: Allocated amount
                    - used_amount: Used amount
                    - remaining_amount: Remaining amount
                    - units: Units string (e.g., 'core-hours')
                - latest_expiration: Latest expiration date across all resources
                - grace_expiration: Grace period end date (YYYY-MM-DD format)
                - facility: Facility name for template selection (e.g., 'UNIV', 'WNA')

                Additional fields can be added and will automatically be available in templates.

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            subject = notification['subject']
            recipient = notification['recipient']
            project_code = notification['project_code']
            facility = notification.get('facility')

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

            # Pass entire notification dict to template (all fields available)
            template_vars = notification.copy()

            # Render text content
            text_content = text_template.render(**template_vars)

            # Create message (multipart if HTML exists, plain text otherwise)
            if html_template:
                # Multipart message with both text and HTML
                msg = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From'] = self.mail_from
                msg['To'] = recipient
                msg['Bcc'] = 'benkirk@ucar.edu'

                html_content = html_template.render(**template_vars)
                msg.attach(MIMEText(text_content, 'plain'))
                msg.attach(MIMEText(html_content, 'html'))
            else:
                # Plain text only
                msg = MIMEText(text_content, 'plain')
                msg['Subject'] = subject
                msg['From'] = self.mail_from
                msg['To'] = recipient
                msg['Bcc'] = 'benkirk@ucar.edu'

            # Send email
            with smtplib.SMTP(self.mail_server, self.mail_port) as smtp:
                if self.mail_use_tls:
                    smtp.starttls()
                if self.mail_username and self.mail_password:
                    smtp.login(self.mail_username, self.mail_password)
                smtp.send_message(msg)

            return (True, None)

        except Exception as e:
            recipient_addr = notification.get('recipient', 'unknown')
            error_msg = f"Failed to send email to {recipient_addr}: {str(e)}"
            return (False, error_msg)

    def send_batch_notifications(self, notifications: List[Dict], dry_run: bool = False) -> Dict:
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
            dry_run: If True, render emails but don't send (returns preview data)

        Returns:
            Dict with 'success' and 'failed' lists containing notification dicts
            In dry_run mode, also includes 'preview_samples' with 1-2 rendered emails
        """
        results = {
            'success': [],
            'failed': []
        }

        # In dry-run mode, collect preview samples (first 2 emails)
        if dry_run:
            results['preview_samples'] = []

        for i, notification in enumerate(notifications):
            if dry_run:
                # Dry-run: render email but don't send
                try:
                    rendered = self._render_email_preview(notification)
                    results['success'].append(notification)

                    # Collect first 2 emails as samples
                    if i < 2:
                        results['preview_samples'].append(rendered)
                except Exception as e:
                    notification['error'] = str(e)
                    results['failed'].append(notification)
            else:
                # Normal mode: actually send email
                success, error = self.send_expiration_notification(notification)

                if success:
                    results['success'].append(notification)
                else:
                    notification['error'] = error
                    results['failed'].append(notification)

        return results

    def _render_email_preview(self, notification: Dict) -> Dict:
        """Render email for preview without sending.

        Args:
            notification: Notification dict (same structure as send_expiration_notification)

        Returns:
            Dict with rendered email content and metadata
        """
        facility = notification.get('facility')

        # Select templates (same logic as send_expiration_notification)
        text_template_name = self._get_template_name('expiration', facility, 'txt')
        html_template_name = self._get_template_name('expiration', facility, 'html')

        # Load text template (required)
        text_template = self.jinja_env.get_template(text_template_name)

        # Try to load HTML template (optional)
        html_template = None
        try:
            html_template = self.jinja_env.get_template(html_template_name)
        except jinja2.exceptions.TemplateNotFound:
            pass

        # Pass entire notification dict to template (all fields available)
        template_vars = notification.copy()

        # Render templates
        text_content = text_template.render(**template_vars)
        html_content = html_template.render(**template_vars) if html_template else None

        return {
            'recipient': notification['recipient'],
            'recipient_name': notification['recipient_name'],
            'recipient_role': notification.get('recipient_role', 'user'),
            'project_code': notification['project_code'],
            'project_title': notification['project_title'],
            'facility': facility,
            'text_template': text_template_name,
            'html_template': html_template_name if html_template else None,
            'text_content': text_content,
            'html_content': html_content
        }
