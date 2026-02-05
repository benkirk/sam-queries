#!/bin/bash
# Test script for email notification functionality
# This demonstrates the notification features without actually sending emails

echo "=== Email Notification Test Script ==="
echo ""

# Load environment
source ../.env

echo "1. Testing upcoming expirations (display only):"
echo "   Command: sam-admin project --upcoming-expirations"
echo ""
sam-admin project --upcoming-expirations | head -20
echo ""

echo "2. Testing with verbose output:"
echo "   Command: sam-admin project --upcoming-expirations --verbose"
echo ""
sam-admin project --upcoming-expirations --verbose | head -30
echo ""

echo "3. Testing with facility filter:"
echo "   Command: sam-admin project --upcoming-expirations --facilities UNIV"
echo ""
sam-admin project --upcoming-expirations --facilities UNIV | head -20
echo ""

echo "4. Dry-run mode (preview emails without sending):"
echo "   Command: sam-admin project --upcoming-expirations --notify --dry-run"
echo ""
sam-admin project --upcoming-expirations --notify --dry-run 2>&1 | head -40
echo ""

echo "5. Dry-run with verbose (shows sample email):"
echo "   Command: sam-admin project --upcoming-expirations --notify --dry-run --verbose"
echo ""
echo "   This shows detailed preview including sample email content"
echo ""

echo "6. Notification command (would send emails if configured):"
echo "   Command: sam-admin project --upcoming-expirations --notify"
echo ""
echo "   Note: This would send emails to project leads, admins, and roster members"
echo "   for all expiring projects. SMTP must be configured in .env"
echo ""

echo "7. Notification with additional recipients:"
echo "   Command: sam-admin project --upcoming-expirations --notify --email-list test@example.com"
echo ""
echo "   Note: This would also send emails to additional recipients"
echo ""

echo "=== Test Complete ==="
echo ""
echo "Recommended workflow:"
echo "  1. Preview first: sam-admin project --upcoming-expirations --notify --dry-run --verbose"
echo "  2. Verify recipients and content"
echo "  3. Send for real: sam-admin project --upcoming-expirations --notify"
echo ""
echo "Note: Ensure .env has correct MAIL_* settings before sending real emails"
