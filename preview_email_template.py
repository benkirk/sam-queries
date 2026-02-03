#!/usr/bin/env python3
"""Preview email templates without sending."""

from cli.core.context import Context
from cli.notifications import EmailNotificationService

# Create context
ctx = Context()

# Create service
service = EmailNotificationService(ctx)

# Sample data for multiple resources
resources = [
    {
        'resource_name': 'Derecho',
        'expiration_date': '2025-02-10',
        'days_remaining': 5,
        'allocated_amount': 1000000.0,
        'used_amount': 850000.0,
        'remaining_amount': 150000.0,
        'units': 'core-hours'
    },
    {
        'resource_name': 'Casper',
        'expiration_date': '2025-02-17',
        'days_remaining': 12,
        'allocated_amount': 50000.0,
        'used_amount': 35000.0,
        'remaining_amount': 15000.0,
        'units': 'core-hours'
    }
]

# Render templates
text_template = service.jinja_env.get_template('expiration.txt')
html_template = service.jinja_env.get_template('expiration.html')

template_vars = {
    'user_name': 'Benjamin Kirk',
    'project_code': 'SCSG0001',
    'project_title': 'CISL Systems Support Group',
    'resources': resources
}

text_content = text_template.render(**template_vars)
html_content = html_template.render(**template_vars)

print("=" * 80)
print("PLAIN TEXT EMAIL PREVIEW")
print("=" * 80)
print(text_content)
print("\n" + "=" * 80)
print("HTML EMAIL PREVIEW (rendered to HTML)")
print("=" * 80)
print(f"Length: {len(html_content)} characters")
print("Contains urgent styling: ", "urgent" in html_content)
print("Contains warning styling: ", "warning" in html_content)
print("\nFirst 500 characters:")
print(html_content[:500])
print("\n[... full HTML content omitted for brevity ...]")
print("\nTo save HTML to file for browser preview:")
print("  python preview_email_template.py > /tmp/email_preview.html")
print("  open /tmp/email_preview.html")
