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
    success, error = email_service.send_expiration_notification(
        recipient='test@example.com',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        recipient_name='Test User'
    )

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

    success, error = service.send_expiration_notification(
        recipient='test@example.com',
        project_code='TEST0001',
        project_title='Test Project',
        resources=resources,
        recipient_name='Test User'
    )

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

    success, error = service.send_expiration_notification(
        recipient='test@example.com',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        recipient_name='Test User'
    )

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
    success, error = email_service.send_expiration_notification(
        recipient='test@example.com',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        recipient_name='Test User'
    )

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
            'recipient_name': 'User One'
        },
        {
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
            'recipient_name': 'User Two'
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
            'recipient_name': 'User One'
        },
        {
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
            'recipient_name': 'User Two'
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
    # Test text template
    text_template = email_service.jinja_env.get_template('expiration.txt')

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    text_content = text_template.render(
        recipient_name='Test User',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources
    )

    # Verify content
    assert 'Test User' in text_content
    assert 'SCSG0001' in text_content
    assert 'Test Project' in text_content
    assert 'Derecho' in text_content
    assert '2025-02-15' in text_content
    assert '12' in text_content

    # Test HTML template
    html_template = email_service.jinja_env.get_template('expiration.html')

    html_content = html_template.render(
        recipient_name='Test User',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources
    )

    # Verify HTML content
    assert 'Test User' in html_content
    assert 'SCSG0001' in html_content
    assert 'Derecho' in html_content
    assert '<html>' in html_content
    assert '</html>' in html_content


@patch('cli.notifications.email.smtplib.SMTP')
def test_multiple_resources_in_single_email(mock_smtp, email_service):
    """Test that multiple resources are included in a single email."""
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

    success, error = email_service.send_expiration_notification(
        recipient='test@example.com',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=resources,
        recipient_name='Test User'
    )

    assert success is True

    # Verify email was sent
    smtp_instance.send_message.assert_called_once()

    # Get the message that was sent
    sent_message = smtp_instance.send_message.call_args[0][0]

    # Verify it's a multipart message with both resources
    message_str = sent_message.as_string()
    assert 'Derecho' in message_str
    assert 'Casper' in message_str


def test_dry_run_mode_does_not_send_emails(email_service):
    """Test that dry-run mode is supported (doesn't send emails at service level)."""
    # This test verifies that the notification service can render templates
    # without actually calling SMTP. The dry-run logic is in the command layer,
    # but we verify templates work for preview mode.

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1000000.0,
        'used_amount': 456789.12,
        'remaining_amount': 543210.88,
        'units': 'core-hours'
    }]

    # Verify we can render templates without sending
    text_template = email_service.jinja_env.get_template('expiration.txt')
    html_template = email_service.jinja_env.get_template('expiration.html')

    context = {
        'recipient_name': 'Test User',
        'project_code': 'TEST0001',
        'project_title': 'Test Project',
        'resources': resources
    }

    text_content = text_template.render(**context)
    html_content = html_template.render(**context)

    # Verify content was rendered
    assert len(text_content) > 0
    assert len(html_content) > 0
    assert 'Test User' in text_content
    assert 'TEST0001' in text_content
    assert 'Derecho' in text_content
