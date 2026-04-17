"""Unit tests for the email notification service.

Ported from tests/unit/test_email_notifications.py. All SMTP calls are
mocked — zero database, zero network. Near-verbatim port; this file
overlaps with test_notification_enhancements.py but tests a different
layer of the service (init, batch/dry-run, template rendering against
shipped templates).
"""
from unittest.mock import MagicMock, patch

import pytest

from cli.core.context import Context
from cli.notifications import EmailNotificationService


pytestmark = pytest.mark.unit


@pytest.fixture
def mock_context():
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
    return EmailNotificationService(mock_context)


# ============================================================================
# Initialization
# ============================================================================


def test_email_service_initialization(email_service, mock_context):
    assert email_service.ctx == mock_context
    assert email_service.mail_server == 'smtp.example.com'
    assert email_service.mail_port == 25
    assert email_service.mail_use_tls is False
    assert email_service.mail_from == 'sam-admin@example.com'
    assert email_service.jinja_env is not None


# ============================================================================
# Single-send SMTP paths
# ============================================================================


def _resource(resource_name='Derecho'):
    return {
        'resource_name': resource_name,
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1_000_000.0,
        'used_amount': 456_789.12,
        'remaining_amount': 543_210.88,
        'units': 'core-hours',
    }


def _notification(**overrides):
    base = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': [_resource()],
        'recipient_name': 'Test User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Lead',
        'project_lead_email': 'lead@example.com',
        'latest_expiration': '2025-02-15',
        'grace_expiration': '2025-05-16',
        'facility': 'UNIV',
    }
    base.update(overrides)
    return base


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_success(mock_smtp, email_service):
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    success, error = email_service.send_expiration_notification(_notification())

    assert success is True
    assert error is None
    mock_smtp.assert_called_once_with('smtp.example.com', 25)
    smtp_instance.send_message.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_with_tls(mock_smtp, mock_context):
    mock_context.mail_use_tls = True
    service = EmailNotificationService(mock_context)

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    success, _err = service.send_expiration_notification(_notification())
    assert success is True
    smtp_instance.starttls.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_with_auth(mock_smtp, mock_context):
    mock_context.mail_username = 'testuser'
    mock_context.mail_password = 'testpass'
    service = EmailNotificationService(mock_context)

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    success, _err = service.send_expiration_notification(_notification())
    assert success is True
    smtp_instance.login.assert_called_once_with('testuser', 'testpass')


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_expiration_notification_failure(mock_smtp, email_service):
    """SMTP failure is captured in the (success, error) tuple."""
    mock_smtp.return_value.__enter__.side_effect = Exception('SMTP connection failed')

    success, error = email_service.send_expiration_notification(_notification())
    assert success is False
    assert error is not None
    assert 'SMTP connection failed' in error


# ============================================================================
# Batch sending
# ============================================================================


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_batch_notifications(mock_smtp, email_service):
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    notifications = [
        _notification(project_code='PROJ0001', recipient='user1@example.com'),
        _notification(project_code='PROJ0002', recipient='user2@example.com',
                      resources=[_resource('Casper')]),
    ]

    results = email_service.send_batch_notifications(notifications)
    assert len(results['success']) == 2
    assert len(results['failed']) == 0
    assert smtp_instance.send_message.call_count == 2


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_batch_notifications_with_failures(mock_smtp, email_service):
    """First notification succeeds, second raises → mixed results."""
    smtp_instance = MagicMock()
    smtp_instance.send_message.side_effect = [None, Exception('Send failed')]
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    notifications = [
        _notification(project_code='PROJ0001', recipient='user1@example.com'),
        _notification(project_code='PROJ0002', recipient='user2@example.com'),
    ]
    results = email_service.send_batch_notifications(notifications)

    assert len(results['success']) == 1
    assert len(results['failed']) == 1
    assert results['failed'][0]['recipient'] == 'user2@example.com'
    assert 'error' in results['failed'][0]


# ============================================================================
# Templates
# ============================================================================


def test_template_rendering(email_service):
    """UNIV text and HTML templates render with our fields."""
    text_template = email_service.jinja_env.get_template('expiration-UNIV.txt')
    html_template = email_service.jinja_env.get_template('expiration-UNIV.html')

    kwargs = dict(
        recipient_name='Test User',
        project_code='SCSG0001',
        project_title='Test Project',
        resources=[_resource()],
        latest_expiration='2025-02-15',
        grace_expiration='2025-05-16',
        recipient_role='user',
        project_lead='Dr. Lead',
        project_lead_email='lead@example.com',
    )
    text_content = text_template.render(**kwargs)
    html_content = html_template.render(**kwargs)

    for field in ('Test User', 'SCSG0001', 'Test Project', '2025-02-15', '2025-05-16',
                  '90 days', 'Dr. Lead', 'lead@example.com'):
        assert field in text_content

    for field in ('Test User', 'SCSG0001', '2025-02-15', '2025-05-16', '<html>', '</html>'):
        assert field in html_content


@patch('cli.notifications.email.smtplib.SMTP')
def test_multiple_resources_in_single_email(mock_smtp, email_service):
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [
        _resource('Derecho'),
        {**_resource('Casper'), 'expiration_date': '2025-02-20', 'days_remaining': 17,
         'allocated_amount': 50_000.0, 'used_amount': 25_000.0, 'remaining_amount': 25_000.0},
    ]
    notification = _notification(resources=resources,
                                 latest_expiration='2025-02-20',
                                 grace_expiration='2025-05-21')
    success, _err = email_service.send_expiration_notification(notification)
    assert success is True
    smtp_instance.send_message.assert_called_once()

    sent_message = smtp_instance.send_message.call_args[0][0]
    message_str = sent_message.as_string()
    assert 'SCSG0001' in message_str
    assert '2025-02-20' in message_str
    assert '2025-05-21' in message_str


# ============================================================================
# Dry-run
# ============================================================================


def test_dry_run_mode_does_not_send_emails(email_service):
    """dry_run=True renders templates and returns previews without sending."""
    notifications = [
        _notification(project_code='TEST0001', recipient='user1@example.com',
                      recipient_name='User One', recipient_role='lead',
                      project_title='Test Project 1',
                      resources=[_resource(resource_name='Derecho')],
                      grace_expiration='2025-05-15'),
        {
            # Minimal, no project_lead_email / facility — tests that dry-run
            # tolerates missing optional fields.
            'subject': 'NSF NCAR Project TEST0002 Expiration Notice',
            'recipient': 'user2@example.com',
            'project_code': 'TEST0002',
            'project_title': 'Test Project 2',
            'resources': [_resource('Casper')],
            'recipient_name': 'User Two',
            'recipient_role': 'user',
            'project_lead': 'Dr. Lead Two',
        },
    ]
    results = email_service.send_batch_notifications(notifications, dry_run=True)

    assert len(results['success']) == 2
    assert len(results['failed']) == 0
    assert 'preview_samples' in results
    assert len(results['preview_samples']) == 2

    sample = results['preview_samples'][0]
    assert sample['recipient'] == 'user1@example.com'
    assert sample['recipient_name'] == 'User One'
    assert sample['recipient_role'] == 'lead'
    assert sample['project_code'] == 'TEST0001'
    assert sample['facility'] == 'UNIV'
    assert 'text_content' in sample
    assert 'Dear User One' in sample['text_content']
    assert 'TEST0001' in sample['text_content']
    assert 'UNIV' in sample['text_template']
