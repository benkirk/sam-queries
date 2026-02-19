#!/bin/bash
set -euo pipefail

# Connect to the staging RDS MySQL database.
# Requires VPN connection to UCAR network (128.117.0.0/16).
#
# Usage:
#   ./scripts/infra/query-staging-db.sh              # interactive mysql session
#   ./scripts/infra/query-staging-db.sh "SELECT 1"   # run a single query

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STAGING_DIR="$REPO_ROOT/infrastructure/staging"

# Get RDS endpoint from Terraform
cd "$STAGING_DIR"
RDS_HOST=$(terraform output -raw rds_address 2>/dev/null)
if [ -z "$RDS_HOST" ]; then
    echo "ERROR: Could not read rds_address. Run 'terraform apply' first."
    exit 1
fi

# Read credentials
DB_USER=$(grep 'db_username' "$STAGING_DIR/secrets.auto.tfvars" | sed 's/.*= *"//' | sed 's/".*//')
DB_PASS=$(grep 'db_password' "$STAGING_DIR/secrets.auto.tfvars" | sed 's/.*= *"//' | sed 's/".*//')

echo "Connecting to staging DB: $RDS_HOST"
echo "Database: sam"
echo ""

if [ $# -gt 0 ]; then
    mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DB_PASS" sam -e "$1"
else
    mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DB_PASS" sam
fi
