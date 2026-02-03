"""Email notification service for SAM CLI."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Tuple, Optional, List, Dict
from jinja2 import Environment, FileSystemLoader


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

    def send_expiration_notification(
        self,
        recipient: str,
        project_code: str,
        project_title: str,
        resources: List[Dict],
        user_name: str
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
            user_name: Recipient's display name

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            # Create multipart message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'SAM Allocation Expiration Notice - {project_code}'
            msg['From'] = self.mail_from
            msg['To'] = recipient

            # Render templates
            text_template = self.jinja_env.get_template('expiration.txt')
            html_template = self.jinja_env.get_template('expiration.html')

            template_vars = {
                'user_name': user_name,
                'project_code': project_code,
                'project_title': project_title,
                'resources': resources
            }

            text_content = text_template.render(**template_vars)
            html_content = html_template.render(**template_vars)

            # Attach both text and HTML versions
            msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))

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
                - user_name: Recipient's display name

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
                user_name=notification['user_name']
            )

            if success:
                results['success'].append(notification)
            else:
                notification['error'] = error
                results['failed'].append(notification)

        return results
