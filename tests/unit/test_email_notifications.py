"""Unit tests for email notification service."""

import pytest
from unittest.mock import MagicMock, patch, call
from cli.core.context import Context
from cli.notifications import EmailNotificationService


@pytest.fixture
def mock_context():
    """Create a mock context for testing."""
    ctx = Context()
    ctx.mail_server = 'smtp.example.com'
    ctx.mail_port = 25
    ctx.mail_use_tls = False
    ctx.mail_username = None
    ctx.mail_password = None
    ctx.mail_from = 'sam-admin@example.com'
    return ctx


@pytest.fixture
def email_service(mock_context):
    """Create email service instance."""
    return EmailNotificationService(mock_context)


def test_email_service_initialization(email_service, mock_context):
    """Test that email service initializes correctly with context."""
    assert email_service.ctx == mock_context
    assert email_service.mail_server == 'smtp.example.com'
    assert email_service.mail_port == 25
    assert email_service.mail_use_tls is False
    assert email_service.mail_from == 'sam-admin@example.com'
    assert email_service.jinja_env is not None


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_success(mock_smtp, email_service):
    """Test successful email sending."""
    # Setup mock
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    # Test data
    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    # Send email
    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-02-15',
        'grace_expiration': '2025-05-16',
        'facility': 'UNIV'
    }
    success, error = email_service.send_expiration_notification(notification)

    # Verify success
    assert success is True
    assert error is None

    # Verify SMTP was called correctly
    mock_smtp.assert_called_once_with('smtp.example.com', 25)
    smtp_instance.send_message.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_with_tls(mock_smtp, mock_context):
    """Test email sending with TLS."""
    # Enable TLS
    mock_context.mail_use_tls = True
    service = EmailNotificationService(mock_context)

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Casper',
        'expiration_date': '2025-03-01',
        'days_remaining': 26,
        'allocated_amount': 50000.0,
        'used_amount': 25000.0,
        'remaining_amount': 25000.0,
        'units': 'core-hours'
    }]

    notification = {
        'subject': 'NSF NCAR Project TEST0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'TEST0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-03-01',
        'grace_expiration': '2025-05-30',
        'facility': 'UNIV'
    }
    success, error = service.send_expiration_notification(notification)

    assert success is True
    smtp_instance.starttls.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_with_auth(mock_smtp, mock_context):
    """Test email sending with SMTP authentication."""
    # Enable auth
    mock_context.mail_username = 'testuser'
    mock_context.mail_password = 'testpass'
    service = EmailNotificationService(mock_context)

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-02-15',
        'grace_expiration': '2025-05-16',
        'facility': 'UNIV'
    }
    success, error = service.send_expiration_notification(notification)

    assert success is True
    smtp_instance.login.assert_called_once_with('testuser', 'testpass')


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_failure(mock_smtp, email_service):
    """Test email sending failure handling."""
    # Setup mock to raise exception
    mock_smtp.return_value.__enter__.side_effect = Exception('SMTP connection failed')

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    # Send email
    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-02-15',
        'grace_expiration': '2025-05-16',
        'facility': 'UNIV'
    }
    success, error = email_service.send_expiration_notification(notification)

    # Verify failure
    assert success is False
    assert error is not None
    assert 'SMTP connection failed' in error


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_batch_notifications(mock_smtp, email_service):
    """Test batch notification sending."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    # Create test notifications
    notifications = [
        {
            'subject': 'NSF NCAR Project PROJ0001 Expiration Notice',
            'recipient': 'user1@example.com',
            'project_code': 'PROJ0001',
            'project_title': 'Project 1',
            'resources': [{
                'resource_name': 'Derecho',
                'expiration_date': '2025-02-15',
                'days_remaining': 12,
                'allocated_amount': 1000000.0,
                'used_amount': 456789.12,
                'remaining_amount': 543210.88,
                'units': 'core-hours'
            }],
            'recipient_name': 'User One',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead One',
            'project_lead_email': 'lead1@example.com',
            'latest_expiration': '2025-02-15',
            'grace_expiration': '2025-05-16',
            'facility': 'UNIV'
        },
        {
            'subject': 'NSF NCAR Project PROJ0002 Expiration Notice',
            'recipient': 'user2@example.com',
            'project_code': 'PROJ0002',
            'project_title': 'Project 2',
            'resources': [{
                'resource_name': 'Casper',
                'expiration_date': '2025-03-01',
                'days_remaining': 26,
                'allocated_amount': 50000.0,
                'used_amount': 25000.0,
                'remaining_amount': 25000.0,
                'units': 'core-hours'
            }],
            'recipient_name': 'User Two',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead Two',
            'project_lead_email': 'lead2@example.com',
            'latest_expiration': '2025-03-01',
            'grace_expiration': '2025-05-30',
            'facility': 'UNIV'
        }
    ]

    # Send batch
    results = email_service.send_batch_notifications(notifications)

    # Verify results
    assert len(results['success']) == 2
    assert len(results['failed']) == 0
    assert smtp_instance.send_message.call_count == 2


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_batch_notifications_with_failures(mock_smtp, email_service):
    """Test batch notifications with some failures."""
    smtp_instance = MagicMock()

    # First call succeeds, second fails
    smtp_instance.send_message.side_effect = [None, Exception('Send failed')]
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    notifications = [
        {
            'subject': 'NSF NCAR Project PROJ0001 Expiration Notice',
            'recipient': 'user1@example.com',
            'project_code': 'PROJ0001',
            'project_title': 'Project 1',
            'resources': [{
                'resource_name': 'Derecho',
                'expiration_date': '2025-02-15',
                'days_remaining': 12,
                'allocated_amount': 1000000.0,
                'used_amount': 456789.12,
                'remaining_amount': 543210.88,
                'units': 'core-hours'
            }],
            'recipient_name': 'User One',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead One',
            'project_lead_email': 'lead1@example.com',
            'latest_expiration': '2025-02-15',
            'grace_expiration': '2025-05-16',
            'facility': 'UNIV'
        },
        {
            'subject': 'NSF NCAR Project PROJ0002 Expiration Notice',
            'recipient': 'user2@example.com',
            'project_code': 'PROJ0002',
            'project_title': 'Project 2',
            'resources': [{
                'resource_name': 'Casper',
                'expiration_date': '2025-03-01',
                'days_remaining': 26,
                'allocated_amount': 50000.0,
                'used_amount': 25000.0,
                'remaining_amount': 25000.0,
                'units': 'core-hours'
            }],
            'recipient_name': 'User Two',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead Two',
            'project_lead_email': 'lead2@example.com',
            'latest_expiration': '2025-03-01',
            'grace_expiration': '2025-05-30',
            'facility': 'UNIV'
        }
    ]

    results = email_service.send_batch_notifications(notifications)

    # Verify mixed results
    assert len(results['success']) == 1
    assert len(results['failed']) == 1
    assert results['failed'][0]['recipient'] == 'user2@example.com'
    assert 'error' in results['failed'][0]


def test_template_rendering(email_service):
    """Test that templates render correctly with data."""
    # Test text template - use UNIV template since it exists
    text_template = email_service.jinja_env.get_template('expiration-UNIV.txt')

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    # Templates now use latest_expiration and grace_expiration instead of individual resources
    text_content = text_template.render(
        recipient_name='Test User',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        latest_expiration='2025-02-15',
        grace_expiration='2025-05-16',
        recipient_role='user',
        project_lead='Dr. Lead',
        project_lead_email='lead@example.com'
    )

    # Verify content - check for fields that ARE in the template
    assert 'Test User' in text_content
    assert 'SCSG0001' in text_content
    assert 'Test Project' in text_content
    assert '2025-02-15' in text_content  # latest_expiration
    assert '2025-05-16' in text_content  # grace_expiration
    assert '90 days' in text_content  # grace period text
    assert 'Dr. Lead' in text_content  # project lead name
    assert 'lead@example.com' in text_content  # project lead email

    # Test HTML template
    html_template = email_service.jinja_env.get_template('expiration-UNIV.html')

    html_content = html_template.render(
        recipient_name='Test User',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        latest_expiration='2025-02-15',
        grace_expiration='2025-05-16',
        recipient_role='user',
        project_lead='Dr. Lead',
        project_lead_email='lead@example.com'
    )

    # Verify HTML content
    assert 'Test User' in html_content
    assert 'SCSG0001' in html_content
    assert '2025-02-15' in html_content
    assert '2025-05-16' in html_content
    assert '<html>' in html_content
    assert '</html>' in html_content


@patch('cli.notifications.email.smtplib.SMTP')
def test_multiple_resources_in_single_email(mock_smtp, email_service):
    """Test that multiple resources are handled in a single email notification."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    # Multiple resources for same project
    resources = [
        {
            'resource_name': 'Derecho',
            'expiration_date': '2025-02-15',
            'days_remaining': 12,
            'allocated_amount': 1000000.0,
            'used_amount': 456789.12,
            'remaining_amount': 543210.88,
            'units': 'core-hours'
        },
        {
            'resource_name': 'Casper',
            'expiration_date': '2025-02-20',
            'days_remaining': 17,
            'allocated_amount': 50000.0,
            'used_amount': 25000.0,
            'remaining_amount': 25000.0,
            'units': 'core-hours'
        }
    ]

    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-02-20',  # Latest of the two resources
        'grace_expiration': '2025-05-21',  # 90 days after latest
        'facility': 'UNIV'
    }
    success, error = email_service.send_expiration_notification(notification)

    assert success is True

    # Verify email was sent
    smtp_instance.send_message.assert_called_once()

    # Get the message that was sent
    sent_message = smtp_instance.send_message.call_args[0][0]

    # Verify it's a multipart message with expected content
    message_str = sent_message.as_string()
    assert 'SCSG0001' in message_str
    assert 'Test Project' in message_str
    assert '2025-02-20' in message_str  # latest_expiration
    assert '2025-05-21' in message_str  # grace_expiration


def test_dry_run_mode_does_not_send_emails(email_service):
    """Test that dry-run mode renders emails but doesn't send them."""
    notifications = [
        {
            'subject': 'NSF NCAR Project TEST0001 Expiration Notice',
            'recipient': 'user1@example.com',
            'project_code': 'TEST0001',
            'project_title': 'Test Project 1',
            'resources': [{
                'resource_name': 'Derecho',
                'expiration_date': '2025-02-15',
                'days_remaining': 10,
                'allocated_amount': 1000000.0,
                'used_amount': 500000.0,
                'remaining_amount': 500000.0,
                'units': 'core-hours'
            }],
            'recipient_name': 'User One',
            'recipient_role': 'lead',
            'project_lead': 'Dr. Lead',
            'grace_expiration': '2025-05-15',
            'facility': 'UNIV'
        },
        {
            'subject': 'NSF NCAR Project TEST0002 Expiration Notice',
            'recipient': 'user2@example.com',
            'project_code': 'TEST0002',
            'project_title': 'Test Project 2',
            'resources': [{
                'resource_name': 'Casper',
                'expiration_date': '2025-03-01',
                'days_remaining': 20,
                'allocated_amount': 50000.0,
                'used_amount': 25000.0,
                'remaining_amount': 25000.0,
                'units': 'core-hours'
            }],
            'recipient_name': 'User Two',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead Two'
        }
    ]

    # Call with dry_run=True
    results = email_service.send_batch_notifications(notifications, dry_run=True)

    # Should have success results
    assert len(results['success']) == 2
    assert len(results['failed']) == 0

    # Should have preview samples (first 2)
    assert 'preview_samples' in results
    assert len(results['preview_samples']) == 2

    # Check first preview sample structure
    sample = results['preview_samples'][0]
    assert sample['recipient'] == 'user1@example.com'
    assert sample['recipient_name'] == 'User One'
    assert sample['recipient_role'] == 'lead'
    assert sample['project_code'] == 'TEST0001'
    assert sample['facility'] == 'UNIV'
    assert 'text_content' in sample
    assert 'Dear User One' in sample['text_content']
    assert 'TEST0001' in sample['text_content']

    # Check that UNIV template was used
    assert 'UNIV' in sample['text_template']
