"""Tests for enhanced notification features.

Covers role-based content, grace-period handling, and facility-specific
email templates. Ported from tests/unit/test_notification_enhancements.py.
All tests mock SMTP and context — no database dependency, no network.

Cleaned up during port: dropped unused `Project/User/Allocation/...` imports
from the legacy file (they were never referenced).
"""
from datetime import datetime, timedelta
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
# Template selection
# ============================================================================


def test_template_fallback_without_facility(email_service):
    """Generic template is used when no facility is specified."""
    assert email_service._get_template_name('expiration', None, 'txt') == 'expiration.txt'


def test_template_fallback_with_missing_facility_template(email_service):
    """Fallback to generic template when facility-specific template doesn't exist."""
    assert email_service._get_template_name('expiration', 'NONEXISTENT', 'txt') == 'expiration.txt'


def test_template_selection_with_facility(email_service):
    """Facility-specific template is used when it exists (UNIV has one shipped)."""
    assert email_service._get_template_name('expiration', 'UNIV', 'txt') == 'expiration-UNIV.txt'
    assert email_service._get_template_name('expiration', 'UNIV', 'html') == 'expiration-UNIV.html'


# ============================================================================
# SMTP send paths
# ============================================================================


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_notification_with_role_and_grace_period(mock_smtp, email_service):
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1_000_000.0,
        'used_amount': 456_789.12,
        'remaining_amount': 543_210.88,
        'units': 'core-hours',
    }]

    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'lead',
        'project_lead': 'Dr. Test Lead',
        'grace_expiration': '2025-05-15',
        'facility': 'UNIV',
    }
    success, error = email_service.send_expiration_notification(notification)
    assert success is True
    assert error is None
    smtp_instance.send_message.assert_called_once()


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_notification_for_user_role(mock_smtp, email_service):
    """User role receives project lead information."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Casper',
        'expiration_date': '2025-03-01',
        'days_remaining': 20,
        'allocated_amount': 50_000.0,
        'used_amount': 25_000.0,
        'remaining_amount': 25_000.0,
        'units': 'core-hours',
    }]
    notification = {
        'subject': 'NSF NCAR Project TEST0001 Expiration Notice',
        'recipient': 'user@example.com',
        'project_code': 'TEST0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Regular User',
        'recipient_role': 'user',
        'project_lead': 'Dr. Project Lead',
        'grace_expiration': '2025-06-01',
    }
    success, error = email_service.send_expiration_notification(notification)
    assert success is True
    assert error is None


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_notification_without_grace_period(mock_smtp, email_service):
    """Grace-period = None sends a notification without grace text."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 10,
        'allocated_amount': 1_000_000.0,
        'used_amount': 0.0,
        'remaining_amount': 1_000_000.0,
        'units': 'core-hours',
    }]
    notification = {
        'subject': 'NSF NCAR Project TEST0002 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'TEST0002',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'lead',
        'grace_expiration': None,
    }
    success, error = email_service.send_expiration_notification(notification)
    assert success is True
    assert error is None


@patch('cli.notifications.email.smtplib.SMTP')
def test_send_text_only_when_html_template_missing(mock_smtp, email_service):
    """Text-only email is sent when the HTML template doesn't exist."""
    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    resources = [{
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-15',
        'days_remaining': 12,
        'allocated_amount': 1_000_000.0,
        'used_amount': 456_789.12,
        'remaining_amount': 543_210.88,
        'units': 'core-hours',
    }]
    notification = {
        'subject': 'NSF NCAR Project SCSG0001 Expiration Notice',
        'recipient': 'test@example.com',
        'project_code': 'SCSG0001',
        'project_title': 'Test Project',
        'resources': resources,
        'recipient_name': 'Test User',
        'recipient_role': 'lead',
        'project_lead': 'Dr. Test Lead',
        'grace_expiration': '2025-05-15',
        'facility': 'NONEXISTENT_FACILITY',
    }
    success, error = email_service.send_expiration_notification(notification)
    assert success is True
    assert error is None
    smtp_instance.send_message.assert_called_once()

    sent_msg = smtp_instance.send_message.call_args[0][0]
    assert sent_msg['Subject'] == 'NSF NCAR Project SCSG0001 Expiration Notice'


# ============================================================================
# Pure-logic helpers (no SMTP)
# ============================================================================


def test_grace_expiration_calculation():
    """Grace expiration is 90 days after the latest resource expiration."""
    base_date = datetime(2025, 2, 15)
    grace_date = base_date + timedelta(days=90)
    assert grace_date.strftime('%Y-%m-%d') == '2025-05-16'


def test_role_determination_logic():
    """Lead/admin/user role is determined by user_id match on project."""
    mock_project = MagicMock()

    lead_user = MagicMock(user_id=1, primary_email='lead@example.com', display_name='Project Lead')
    admin_user = MagicMock(user_id=2, primary_email='admin@example.com', display_name='Project Admin')
    regular_user = MagicMock(user_id=3, primary_email='user@example.com', display_name='Regular User')

    mock_project.lead = lead_user
    mock_project.admin = admin_user
    mock_project.roster = [lead_user, admin_user, regular_user]

    def _role_for(user):
        if mock_project.lead and user.user_id == mock_project.lead.user_id:
            return 'lead'
        if mock_project.admin and user.user_id == mock_project.admin.user_id:
            return 'admin'
        return 'user'

    assert _role_for(lead_user) == 'lead'
    assert _role_for(admin_user) == 'admin'
    assert _role_for(regular_user) == 'user'


def test_facility_extraction_logic():
    """Facility is extracted from project.allocation_type.panel.facility."""
    mock_facility = MagicMock()
    mock_facility.facility_name = 'UNIV'
    mock_panel = MagicMock()
    mock_panel.facility = mock_facility
    mock_allocation_type = MagicMock()
    mock_allocation_type.panel = mock_panel
    mock_project = MagicMock()
    mock_project.allocation_type = mock_allocation_type

    facility_name = None
    if mock_project.allocation_type and mock_project.allocation_type.panel and mock_project.allocation_type.panel.facility:
        facility_name = mock_project.allocation_type.panel.facility.facility_name
    assert facility_name == 'UNIV'


def test_facility_extraction_with_missing_allocation_type():
    mock_project = MagicMock()
    mock_project.allocation_type = None

    facility_name = None
    if mock_project.allocation_type and mock_project.allocation_type.panel and mock_project.allocation_type.panel.facility:
        facility_name = mock_project.allocation_type.panel.facility.facility_name
    assert facility_name is None


def test_facility_extraction_with_missing_panel():
    mock_allocation_type = MagicMock()
    mock_allocation_type.panel = None
    mock_project = MagicMock()
    mock_project.allocation_type = mock_allocation_type

    facility_name = None
    if mock_project.allocation_type and mock_project.allocation_type.panel and mock_project.allocation_type.panel.facility:
        facility_name = mock_project.allocation_type.panel.facility.facility_name
    assert facility_name is None
