#!/bin/bash
set -euo pipefail

# Connect to the staging RDS MySQL database.
# Requires UCAR VPN connection (128.117.0.0/16).
#
# Usage:
#   ./scripts/infra/query-staging-db.sh              # interactive mysql session
#   ./scripts/infra/query-staging-db.sh "SELECT 1"   # run a single query
#   ./scripts/infra/query-staging-db.sh --ssm         # auto-fetch creds from AWS SSM
#
# Environment variables (optional):
#   SAM_STAGING_DB_HOST     - RDS hostname (has default)
#   SAM_STAGING_DB_USER     - DB username  (has default)
#   SAM_STAGING_DB_PASSWORD - DB password  (prompted if not set)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/prereqs.sh"

DEFAULT_HOST="sam-staging-mysql.c0ntwnweue47.us-east-1.rds.amazonaws.com"
DEFAULT_USER="samadmin"
DEFAULT_DB="sam"
DEFAULT_PORT=3306

SSM_MODE=false
QUERY=""

for arg in "$@"; do
    case "$arg" in
        --ssm)
            SSM_MODE=true
            ;;
        *)
            QUERY="$arg"
            ;;
    esac
done

# --- Prerequisites ---
require_cmd mysql mysql-client mysql-client "MySQL client"

DB_HOST="${SAM_STAGING_DB_HOST:-$DEFAULT_HOST}"
DB_USER="${SAM_STAGING_DB_USER:-$DEFAULT_USER}"
DBPW="${SAM_STAGING_DB_PASSWORD:-}"

if [ "$SSM_MODE" = true ]; then
    check_aws_cli
    echo "Fetching credentials from AWS SSM..."
    SSM_PREFIX="/sam/staging/db"
    SSM_PW="${SSM_PREFIX}-password"
    DB_HOST=$(aws ssm get-parameter --name "${SSM_PREFIX}-host" --query 'Parameter.Value' --output text --region us-east-1)
    DB_USER=$(aws ssm get-parameter --name "${SSM_PREFIX}-username" --with-decryption --query 'Parameter.Value' --output text --region us-east-1)
    DBPW=$(aws ssm get-parameter --name "$SSM_PW" --with-decryption --query 'Parameter.Value' --output text --region us-east-1)
fi

if [ -z "$DBPW" ]; then
    read -s -p "Staging DB credential: " DBPW
    echo
fi

check_vpn "$DB_HOST" "$DEFAULT_PORT"

echo "Connecting to staging DB: $DB_HOST"
echo "Database: $DEFAULT_DB"
echo ""

if [ -n "$QUERY" ]; then
    mysql -h "$DB_HOST" -P "$DEFAULT_PORT" -u "$DB_USER" -p"$DBPW" "$DEFAULT_DB" -e "$QUERY"
else
    mysql -h "$DB_HOST" -P "$DEFAULT_PORT" -u "$DB_USER" -p"$DBPW" "$DEFAULT_DB"
fi
