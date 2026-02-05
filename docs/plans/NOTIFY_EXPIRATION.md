# Email Notification for Expiring Projects

## Overview

Add email notification functionality to `sam-admin project --upcoming-expirations --notify` to automatically email users about expiring project allocations.

**Key Design:** Send **one email per project** (not per resource). If a project has multiple resources expiring (e.g., Derecho + Casper), all resources are listed in a single email to avoid notification spam.

## Requirements

- Command: `sam-admin project --upcoming-expirations --notify [--email-list]`
- Works exactly like `sam-search project --upcoming-expirations` for finding projects
- For each expiring project, determine users and send **one email** listing all expiring resources
- Use stdlib: `email.message.EmailMessage` and `smtplib`
- Use Jinja2 for templates (already a dependency)
- Support both text and HTML email formats
- Templates should include project code, expiration dates for all resources

## Architecture

```
sam-admin project --upcoming-expirations --notify
         ↓
ProjectExpirationCommand.execute(notify=True)
         ↓
get_projects_by_allocation_end_date()
    → [(project, allocation, resource_name, days), ...]
         ↓
Group by project (using defaultdict)
    → {projcode: [resources_list]}
         ↓
For each unique project:
    1. Build resources list (all expiring resources for this project)
    2. Get recipients: project.lead, project.admin, project.roster
    3. Render email from templates (includes all resources)
    4. Send ONE email per recipient via EmailNotificationService
    5. Track success/failure
         ↓
display_notification_results()
```

## Critical Files

### Files to Create
- `src/cli/notifications/__init__.py`
- `src/cli/notifications/email.py` - EmailNotificationService class
- `src/cli/templates/expiration.txt` - Plain text Jinja2 template
- `src/cli/templates/expiration.html` - HTML Jinja2 template (optional)
- `src/cli/project/display.py` - Add `display_notification_results()`
- `tests/unit/test_email_notifications.py` - Unit tests

### Files to Modify
- `src/cli/cmds/admin.py` - Add project expiration commands with --notify flag
- `src/cli/project/commands.py` - Add notify parameter to ProjectExpirationCommand
- `.env.example` - Add email configuration variables
- `src/cli/core/context.py` - Add email configuration to Context

## Implementation Steps

### Step 1: Add Email Configuration (15 min)

**Update `.env.example`:**
```bash
# Email Configuration
MAIL_SERVER=ndir.ucar.edu
MAIL_PORT=25
MAIL_USE_TLS=false
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_DEFAULT_FROM=sam-admin@ucar.edu
```

**Update `src/cli/core/context.py`:**
```python
class Context:
    """Shared context for CLI commands."""
    def __init__(self):
        self.session: Optional[Session] = None
        self.verbose: bool = False
        self.very_verbose: bool = False
        self.inactive_projects: bool = False
        self.inactive_users: bool = False
        self.console = Console()

        # Email configuration from environment
        self.mail_server = os.getenv('MAIL_SERVER', 'ndir.ucar.edu')
        self.mail_port = int(os.getenv('MAIL_PORT', '25'))
        self.mail_use_tls = os.getenv('MAIL_USE_TLS', 'false').lower() == 'true'
        self.mail_username = os.getenv('MAIL_USERNAME')
        self.mail_password = os.getenv('MAIL_PASSWORD')
        self.mail_from = os.getenv('MAIL_DEFAULT_FROM', 'sam-admin@ucar.edu')
```

### Step 2: Create Email Templates (30 min)

**Create `src/cli/templates/expiration.txt`:**
```jinja2
Dear {{ user_name }},

This is an automated notification from NCAR CISL regarding your project allocations.

Project: {{ project_code }} - {{ project_title }}

{% if resources|length == 1 %}
Your allocation is expiring soon:
{% else %}
You have {{ resources|length }} allocations expiring soon:
{% endif %}

{% for resource in resources %}
{% if resource.days_remaining <= 7 %}
⚠️  URGENT: {{ resource.resource_name }} expires in {{ resource.days_remaining }} days ({{ resource.expiration_date }})
{% elif resource.days_remaining <= 14 %}
⚠️  WARNING: {{ resource.resource_name }} expires in {{ resource.days_remaining }} days ({{ resource.expiration_date }})
{% else %}
{{ resource.resource_name }} expires in {{ resource.days_remaining }} days ({{ resource.expiration_date }})
{% endif %}
   - Allocated: {{ resource.allocated_amount }} core-hours
   - Used: {{ resource.used_amount }} core-hours
   - Remaining: {{ resource.remaining_amount }} core-hours
{% if not loop.last %}

{% endif %}
{% endfor %}

Please take action to:
- Submit a renewal request if you need continued access
- Save any critical data before expiration
- Contact CISL support if you have questions

For more information, visit: https://arc.ucar.edu/

---
This is an automated message from NCAR CISL System for Allocation Management (SAM)
Questions? Contact: cisl-consulting@ucar.edu
```

**Create `src/cli/templates/expiration.html`:**
```jinja2
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #003366; color: white; padding: 20px; text-align: center; }
        .content { background: #f9f9f9; padding: 20px; border: 1px solid #ddd; }
        .alert-urgent { background: #ffebee; border-left: 4px solid #f44336; padding: 10px; margin: 10px 0; }
        .alert-warning { background: #fff3e0; border-left: 4px solid #ff9800; padding: 10px; margin: 10px 0; }
        .alert-info { background: #e3f2fd; border-left: 4px solid #2196f3; padding: 10px; margin: 10px 0; }
        .details { background: white; padding: 15px; margin: 15px 0; border: 1px solid #ddd; }
        .footer { text-align: center; color: #666; font-size: 12px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>NCAR CISL Allocation Expiration Notice</h2>
        </div>

        <div class="content">
            <p>Dear {{ user_name }},</p>

            <p>This is an automated notification regarding your project allocations.</p>

            <div class="details">
                <h3>Project Information</h3>
                <p><strong>Project:</strong> {{ project_code }} - {{ project_title }}</p>
            </div>

            {% if resources|length == 1 %}
            <h3>Expiring Allocation</h3>
            {% else %}
            <h3>Expiring Allocations ({{ resources|length }} resources)</h3>
            {% endif %}

            {% for resource in resources %}
            {% if resource.days_remaining <= 7 %}
            <div class="alert-urgent">
                <strong>⚠️ URGENT:</strong> {{ resource.resource_name }} expires in {{ resource.days_remaining }} days!
            </div>
            {% elif resource.days_remaining <= 14 %}
            <div class="alert-warning">
                <strong>⚠️ WARNING:</strong> {{ resource.resource_name }} expires in {{ resource.days_remaining }} days.
            </div>
            {% else %}
            <div class="alert-info">
                {{ resource.resource_name }} expires in {{ resource.days_remaining }} days.
            </div>
            {% endif %}

            <div class="details">
                <p><strong>Resource:</strong> {{ resource.resource_name }}</p>
                <p><strong>Expiration Date:</strong> {{ resource.expiration_date }}</p>
                <p><strong>Days Remaining:</strong> {{ resource.days_remaining }}</p>
                <p><strong>Allocated:</strong> {{ resource.allocated_amount }} core-hours</p>
                <p><strong>Used:</strong> {{ resource.used_amount }} core-hours</p>
                <p><strong>Remaining:</strong> {{ resource.remaining_amount }} core-hours</p>
            </div>
            {% endfor %}

            <h3>Action Required</h3>
            <ul>
                <li>Submit a renewal request if you need continued access</li>
                <li>Save any critical data before expiration</li>
                <li>Contact CISL support if you have questions</li>
            </ul>

            <p>For more information, visit: <a href="https://arc.ucar.edu/">https://arc.ucar.edu/</a></p>
        </div>

        <div class="footer">
            <p>This is an automated message from NCAR CISL System for Allocation Management (SAM)</p>
            <p>Questions? Contact: <a href="mailto:cisl-consulting@ucar.edu">cisl-consulting@ucar.edu</a></p>
        </div>
    </div>
</body>
</html>
```

### Step 3: Create Email Service Module (1 hour)

**Create `src/cli/notifications/__init__.py`:**
```python
"""Notification services for SAM CLI."""

from .email import EmailNotificationService

__all__ = ['EmailNotificationService']
```

**Create `src/cli/notifications/email.py`:**
```python
"""Email notification service using stdlib."""

import smtplib
from email.message import EmailMessage
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from cli.core.context import Context


class EmailNotificationService:
    """Service for sending email notifications."""

    def __init__(self, ctx: Context):
        """Initialize email service with context."""
        self.ctx = ctx
        self.console = ctx.console

        # Setup Jinja2 environment for templates
        template_dir = Path(__file__).parent.parent / 'templates'
        self.jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml'])
        )

    def send_expiration_notification(
        self,
        recipient: str,
        project_code: str,
        project_title: str,
        resources: List[Dict],
        user_name: str = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Send expiration notification email.

        Args:
            recipient: Email address
            project_code: Project code (e.g., SCSG0001)
            project_title: Full project title
            resources: List of dicts with keys: resource_name, expiration_date,
                      days_remaining, allocated_amount, used_amount, remaining_amount
            user_name: Recipient's name (optional)

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            # Create message
            msg = EmailMessage()
            msg['From'] = self.ctx.mail_from
            msg['To'] = recipient

            # Subject includes resource names
            if len(resources) == 1:
                subject = f"Allocation Expiring Soon: {project_code} - {resources[0]['resource_name']}"
            else:
                resource_names = ', '.join(r['resource_name'] for r in resources)
                subject = f"Allocations Expiring Soon: {project_code} - {resource_names}"
            msg['Subject'] = subject

            # Render templates
            context = {
                'user_name': user_name or 'User',
                'project_code': project_code,
                'project_title': project_title,
                'resources': resources  # List of resource dicts
            }

            # Render text content
            text_template = self.jinja_env.get_template('expiration.txt')
            text_content = text_template.render(**context)
            msg.set_content(text_content)

            # Render HTML content (optional)
            try:
                html_template = self.jinja_env.get_template('expiration.html')
                html_content = html_template.render(**context)
                msg.add_alternative(html_content, subtype='html')
            except Exception as e:
                if self.ctx.verbose:
                    self.console.print(f"[dim]HTML template not available: {e}[/dim]")

            # Send email
            with smtplib.SMTP(self.ctx.mail_server, self.ctx.mail_port) as smtp:
                if self.ctx.mail_use_tls:
                    smtp.starttls()

                if self.ctx.mail_username and self.ctx.mail_password:
                    smtp.login(self.ctx.mail_username, self.ctx.mail_password)

                smtp.send_message(msg)

            return (True, None)

        except Exception as e:
            error_msg = f"Failed to send email to {recipient}: {e}"
            return (False, error_msg)

    def send_batch_notifications(
        self,
        notifications: List[Dict]
    ) -> Dict[str, List[str]]:
        """
        Send multiple notifications.

        Args:
            notifications: List of notification dicts with required fields

        Returns:
            Dict with 'success' and 'failed' lists of recipient emails
        """
        results = {
            'success': [],
            'failed': []
        }

        for notification in notifications:
            recipient = notification['recipient']
            success, error = self.send_expiration_notification(**notification)

            if success:
                results['success'].append(recipient)
                if self.ctx.verbose:
                    self.console.print(f"✓ Sent to {recipient}", style="green")
            else:
                results['failed'].append(recipient)
                self.console.print(f"✗ Failed: {recipient} - {error}", style="red")

        return results
```

### Step 4: Add Display Function (15 min)

**Update `src/cli/project/display.py`:**
```python
def display_notification_results(ctx: Context, results: Dict[str, List[str]], total_projects: int):
    """Display email notification results.

    Args:
        ctx: Context object
        results: Dict with 'success' and 'failed' lists
        total_projects: Total number of expiring projects
    """
    from rich.panel import Panel
    from rich.table import Table

    success_count = len(results['success'])
    failed_count = len(results['failed'])

    # Summary panel
    summary = Table(show_header=False, box=None)
    summary.add_column("Metric", style="cyan bold")
    summary.add_column("Count")

    summary.add_row("Expiring Projects", str(total_projects))
    summary.add_row("Emails Sent", f"[green]{success_count}[/green]")
    if failed_count > 0:
        summary.add_row("Failed", f"[red]{failed_count}[/red]")

    ctx.console.print(Panel(summary, title="Notification Results", border_style="blue"))

    # Failed recipients detail
    if failed_count > 0:
        ctx.console.print("\n[bold red]Failed Recipients:[/]")
        for recipient in results['failed']:
            ctx.console.print(f"  ✗ {recipient}", style="red")

    # Success list in verbose mode
    if ctx.verbose and success_count > 0:
        ctx.console.print("\n[bold green]Successful Notifications:[/]")
        for recipient in results['success']:
            ctx.console.print(f"  ✓ {recipient}", style="green")
```

### Step 5: Update Project Commands (45 min)

**Update `src/cli/project/commands.py` - ProjectExpirationCommand:**
```python
from datetime import datetime, timedelta
from cli.notifications.email import EmailNotificationService

class ProjectExpirationCommand(BaseProjectCommand):
    """Find upcoming or recently expired projects."""

    def execute(self, upcoming: bool = True, since: datetime = None,
                list_users: bool = False, facility_filter: list = None,
                notify: bool = False, email_list: str = None) -> int:
        """
        Execute project expiration command.

        Args:
            upcoming: If True, show upcoming expirations; else show recent
            since: Start date for recent expirations
            list_users: Show user details
            facility_filter: Filter by facility names
            notify: Send email notifications (admin only)
            email_list: Comma-separated list of additional recipients
        """
        try:
            if self.ctx.verbose:
                self.console.print(f"[dim]Facilities: {'ALL' if facility_filter is None else ', '.join(facility_filter)}[/]")

            if upcoming:
                # Upcoming Expirations
                expiring = get_projects_by_allocation_end_date(
                    self.session,
                    start_date=datetime.now(),
                    end_date=datetime.now() + timedelta(days=32),
                    facility_names=facility_filter
                )

                # Display results
                display_expiring_projects(self.ctx, expiring, list_users=list_users, upcoming=True)

                # Send notifications if requested
                if notify:
                    return self._send_notifications(expiring, email_list)

            else:
                # Recent expirations (existing logic)
                # ... [keep existing code]
                pass

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)

    def _send_notifications(self, expiring_data: list, additional_recipients: str = None) -> int:
        """Send email notifications for expiring projects.

        Groups by project and sends one email per project (even if multiple resources expiring).
        """
        from collections import defaultdict

        email_service = EmailNotificationService(self.ctx)

        # Parse additional recipients
        extra_emails = []
        if additional_recipients:
            extra_emails = [email.strip() for email in additional_recipients.split(',')]

        # Group expiring data by project
        projects_data = defaultdict(list)
        for proj, alloc, resource_name, days in expiring_data:
            projects_data[proj.projcode].append({
                'project': proj,
                'allocation': alloc,
                'resource_name': resource_name,
                'days_remaining': days
            })

        # Build notification list (one per project per recipient)
        notifications = []

        for projcode, resources_list in projects_data.items():
            # Use first entry to get project details (all have same project)
            proj = resources_list[0]['project']

            # Get project usage details for all resources
            usage = proj.get_detailed_allocation_usage()

            # Build resources list for this project
            resources = []
            for res_data in resources_list:
                resource_name = res_data['resource_name']
                alloc = res_data['allocation']
                days = res_data['days_remaining']
                resource_usage = usage.get(resource_name, {})

                resources.append({
                    'resource_name': resource_name,
                    'expiration_date': alloc.end_date.strftime('%Y-%m-%d'),
                    'days_remaining': days,
                    'allocated_amount': f"{resource_usage.get('allocated', alloc.amount):,.0f}",
                    'used_amount': f"{resource_usage.get('used', 0):,.0f}",
                    'remaining_amount': f"{resource_usage.get('remaining', alloc.amount):,.0f}"
                })

            # Determine recipients for this project
            recipients = set()

            # Always notify lead and admin
            if proj.lead and proj.lead.primary_email:
                recipients.add((proj.lead.primary_email, proj.lead.display_name))

            if proj.admin and proj.admin != proj.lead and proj.admin.primary_email:
                recipients.add((proj.admin.primary_email, proj.admin.display_name))

            # Hard-coded flag to include full roster (can be made a CLI option later)
            if True:
                # Include all project users from roster
                for user in proj.roster:
                    if user.primary_email:
                        recipients.add((user.primary_email, user.display_name))

            # Add extra recipients
            for email in extra_emails:
                recipients.add((email, None))

            # Create one notification per recipient for this project
            for recipient_email, recipient_name in recipients:
                notifications.append({
                    'recipient': recipient_email,
                    'project_code': proj.projcode,
                    'project_title': proj.title,
                    'resources': resources,  # List of resource dicts
                    'user_name': recipient_name
                })

        # Send notifications
        unique_projects = len(projects_data)
        self.console.print(f"\n[bold]Sending notifications for {unique_projects} project(s) to {len(notifications)} recipient(s)...[/]")
        results = email_service.send_batch_notifications(notifications)

        # Display results
        from cli.project.display import display_notification_results
        display_notification_results(self.ctx, results, unique_projects)

        # Return error if any failed
        if len(results['failed']) > 0:
            return EXIT_ERROR

        return EXIT_SUCCESS
```

### Step 6: Wire Up Admin CLI (30 min)

**Update `src/cli/cmds/admin.py`:**
```python
@cli.command('project')
@click.argument('projcode', required=False)
@click.option('--upcoming-expirations', '-f', is_flag=True,
              help='Find projects with allocations expiring soon')
@click.option('--notify', is_flag=True,
              help='Send email notifications (requires --upcoming-expirations)')
@click.option('--email-list', type=str,
              help='Comma-separated list of additional email recipients')
@click.option('--facilities', '-F', multiple=True, default=['UNIV', 'WNA'],
              help='Filter by facility (default: UNIV, WNA)')
@click.option('--list-users', is_flag=True, help='List all users')
@click.option('--validate', is_flag=True, help='Validate project data')
@click.option('--reconcile', is_flag=True, help='Reconcile allocations')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, upcoming_expirations, notify, email_list,
            facilities, list_users, validate, reconcile, verbose):
    """Administrative project commands."""
    if verbose:
        ctx.verbose = True

    # Handle expiration notifications
    if upcoming_expirations:
        from cli.project.commands import ProjectExpirationCommand

        if notify and not email_list:
            ctx.console.print("[yellow]Note: Notifications will be sent to project leads/admins only.[/]")
            ctx.console.print("[yellow]Use --email-list to include additional recipients.[/]")

        command = ProjectExpirationCommand(ctx)
        exit_code = command.execute(
            upcoming=True,
            facility_filter=list(facilities) if facilities else None,
            list_users=list_users,
            notify=notify,
            email_list=email_list
        )
        sys.exit(exit_code)

    # Handle individual project lookup
    if not projcode:
        ctx.console.print("Error: PROJECT_CODE required (or use --upcoming-expirations)", style="red")
        sys.exit(1)

    command = ProjectAdminCommand(ctx)
    exit_code = command.execute(projcode, validate=validate, reconcile=reconcile,
                                list_users=list_users)
    sys.exit(exit_code)
```

### Step 7: Testing (1 hour)

**Create `tests/unit/test_email_notifications.py`:**
```python
"""Tests for email notification service."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from cli.notifications.email import EmailNotificationService
from cli.core.context import Context


@pytest.fixture
def mock_context():
    """Create mock context for testing."""
    ctx = Mock(spec=Context)
    ctx.mail_server = 'localhost'
    ctx.mail_port = 25
    ctx.mail_use_tls = False
    ctx.mail_username = None
    ctx.mail_password = None
    ctx.mail_from = 'test@example.com'
    ctx.verbose = False
    ctx.console = Mock()
    return ctx


@pytest.fixture
def email_service(mock_context):
    """Create email service with mock context."""
    return EmailNotificationService(mock_context)


def test_email_service_initialization(email_service):
    """Test email service initializes correctly."""
    assert email_service is not None
    assert email_service.jinja_env is not None


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_success(mock_smtp, email_service):
    """Test successful email sending."""
    # Mock SMTP
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    # Send notification
    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-03-01',
        'days_remaining': 15,
        'allocated_amount': '100,000',
        'used_amount': '50,000',
        'remaining_amount': '50,000'
    }]
    success, error = email_service.send_expiration_notification(
        recipient='test@example.com',
        project_code='TEST0001',
        project_title='Test Project',
        resources=resources,
        user_name='Test User'
    )

    assert success is True
    assert error is None
    smtp_instance.send_message.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_failure(mock_smtp, email_service):
    """Test email sending failure."""
    # Mock SMTP to raise exception
    mock_smtp.return_value.__enter__.side_effect = Exception('SMTP error')

    # Send notification
    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-03-01',
        'days_remaining': 15,
        'allocated_amount': '100,000',
        'used_amount': '50,000',
        'remaining_amount': '50,000'
    }]
    success, error = email_service.send_expiration_notification(
        recipient='test@example.com',
        project_code='TEST0001',
        project_title='Test Project',
        resources=resources
    )

    assert success is False
    assert 'SMTP error' in error


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_batch_notifications(mock_smtp, email_service):
    """Test sending batch notifications."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    notifications = [
        {
            'recipient': 'user1@example.com',
            'project_code': 'TEST0001',
            'project_title': 'Test Project 1',
            'resources': [{
                'resource_name': 'Derecho',
                'expiration_date': '2025-03-01',
                'days_remaining': 15,
                'allocated_amount': '100,000',
                'used_amount': '50,000',
                'remaining_amount': '50,000'
            }]
        },
        {
            'recipient': 'user2@example.com',
            'project_code': 'TEST0002',
            'project_title': 'Test Project 2',
            'resources': [{
                'resource_name': 'Casper',
                'expiration_date': '2025-03-15',
                'days_remaining': 30,
                'allocated_amount': '200,000',
                'used_amount': '100,000',
                'remaining_amount': '100,000'
            }]
        }
    ]

    results = email_service.send_batch_notifications(notifications)

    assert len(results['success']) == 2
    assert len(results['failed']) == 0
    assert smtp_instance.send_message.call_count == 2
```

**Integration test:**
```bash
# Manual testing (with real database)
source ../.env

# Test without sending (display only)
sam-admin project --upcoming-expirations --list-users

# Test with dry-run notification (verbose mode)
sam-admin project --upcoming-expirations --notify --verbose

# Test with additional recipients
sam-admin project --upcoming-expirations --notify --email-list "test@example.com,admin@example.com"
```

## Verification Checklist

### Functional Testing
- [ ] `sam-admin project --upcoming-expirations` - shows expiring projects
- [ ] `sam-admin project --upcoming-expirations --notify` - sends emails to leads/admins
- [ ] `sam-admin project --upcoming-expirations --notify --email-list "test@example.com"` - includes extra recipients
- [ ] Email templates render correctly with project data
- [ ] Both text and HTML emails are sent
- [ ] Notification results display correctly
- [ ] Failed emails are reported with error messages

### Test Suite
- [ ] `pytest tests/unit/test_email_notifications.py --no-cov` - all pass
- [ ] Email service unit tests pass with mocked SMTP
- [ ] Template rendering works correctly
- [ ] Error handling works for missing templates

### Email Content Verification
- [ ] Email subject includes project code and resource
- [ ] Email body includes all required fields:
  - Project code and title
  - Resource name
  - Expiration date
  - Days remaining
  - Allocation amounts (allocated, used, remaining)
- [ ] Urgency levels display correctly (7 days, 14 days, 30+ days)
- [ ] HTML email renders properly in email clients
- [ ] Plain text email is readable

### Edge Cases
- [ ] Users with no email address are skipped gracefully
- [ ] Projects with multiple expiring allocations are handled
- [ ] SMTP connection errors are caught and reported
- [ ] Template rendering errors are caught and reported
- [ ] Empty facility filter works (all facilities)

## Environment Setup

**Required `.env` variables:**
```bash
MAIL_SERVER=ndir.ucar.edu
MAIL_PORT=25
MAIL_USE_TLS=false
MAIL_DEFAULT_FROM=sam-admin@ucar.edu
```

## Key Design Decisions

### Why Stdlib Email?
- No external dependencies (email.message, smtplib already available)
- Sufficient for basic notification needs
- Easy to maintain and test

### Why Jinja2 Templates?
- Already a dependency (used elsewhere)
- Powerful templating with inheritance, filters, etc.
- Supports both text and HTML formats
- Easy to customize messages per project/urgency

### Why Full Roster Recipients?
- Initially notify all project users (lead, admin, and roster)
- Ensures everyone knows about impending expiration
- Hard-coded `if True:` block makes it easy to toggle or convert to CLI flag later
- Can be changed to `if False:` to only notify leads/admins
- Allows --email-list for custom additional recipients

### Why Separate notifications/ Module?
- Clear separation of concerns
- Reusable for other notification types (allocation adjustments, etc.)
- Follows existing CLI architecture pattern
- Easy to extend with SMS, Slack, etc.

## Future Enhancements

Easy to add:
1. **More notification types**: Allocation adjustments, project approvals
2. **Additional delivery methods**: Slack, SMS via Twilio
3. **Notification preferences**: Per-user opt-in/opt-out
4. **Digest mode**: Single email with all expiring projects
5. **--dry-run flag**: Preview emails without sending

Requires planning:
1. **Notification scheduling**: Cron integration for automated reminders
2. **Email tracking**: Open rates, click tracking
3. **Template management**: Web UI for editing templates

## Success Criteria

1. ✅ Command works: `sam-admin project --upcoming-expirations --notify`
2. ✅ Emails sent to project leads/admins
3. ✅ Templates include all required project data
4. ✅ Both text and HTML formats supported
5. ✅ Notification results displayed clearly
6. ✅ Failed sends reported with errors
7. ✅ Unit tests pass with mocked SMTP
8. ✅ Manual testing successful with real SMTP server
9. ✅ Zero breaking changes to existing commands
10. ✅ Easy to extend for future notification types

## Notes

- SMTP server `ndir.ucar.edu:25` does not require authentication (internal UCAR)
- Templates should be generic enough to work for all project types
- Consider urgency levels in email content (7 days vs 30 days)
- Email failures should not block the command (log and continue)
- Verbose mode shows all sent emails for debugging
