#!/bin/bash
set -euo pipefail

# Fetch staging DB credentials from AWS SSM Parameter Store.
# Requires AWS CLI with access to the sam-queries AWS account.
#
# Use this to retrieve credentials for sharing with the team
# or configuring third-party database tools (DBeaver, TablePlus, etc.)
#
# Usage:
#   ./scripts/infra/db-creds-staging.sh          # display all formats
#   ./scripts/infra/db-creds-staging.sh --env     # output as env exports only
#   ./scripts/infra/db-creds-staging.sh --json    # output as JSON

REGION="us-east-1"
SSM_PREFIX="/sam/staging"

if ! command -v aws &>/dev/null; then
    echo "ERROR: AWS CLI is required. This script is for team members with AWS access."
    echo "  Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 2
fi

FORMAT="${1:-all}"

echo "Fetching staging credentials from SSM ($SSM_PREFIX/*)..."
echo ""

SSM_DB_PREFIX="${SSM_PREFIX}/db"
DB_HOST=$(aws ssm get-parameter --name "${SSM_DB_PREFIX}-host" --query 'Parameter.Value' --output text --region "$REGION")
DB_USER=$(aws ssm get-parameter --name "${SSM_DB_PREFIX}-username" --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
# gitguardian:ignore Generic Database Assignment
DBPW=$(aws ssm get-parameter --name "${SSM_DB_PREFIX}-password" --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
DB_PORT=3306
DB_NAME="sam"

case "$FORMAT" in
    --env)
        echo "export SAM_STAGING_DB_HOST=\"$DB_HOST\""
        echo "export SAM_STAGING_DB_USER=\"$DB_USER\""
        echo "export SAM_STAGING_DB_PASSWORD=\"$DBPW\""
        ;;
    --json)
        cat <<EOF
{
  "host": "$DB_HOST",
  "port": $DB_PORT,
  "database": "$DB_NAME",
  "username": "$DB_USER",
  "password": "$DBPW"
}
EOF
        ;;
    *)
        echo "=== Staging Database Credentials ==="
        echo ""
        echo "  Host:     $DB_HOST"
        echo "  Port:     $DB_PORT"
        echo "  Database: $DB_NAME"
        echo "  Username: $DB_USER"
        echo "  Password: $DBPW"
        echo ""
        echo "=== MySQL Connection String ==="
        echo ""
        echo "  mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p'$DBPW' $DB_NAME"
        echo ""
        echo "=== Environment Variables ==="
        echo ""
        echo "  export SAM_STAGING_DB_HOST=\"$DB_HOST\""
        echo "  export SAM_STAGING_DB_USER=\"$DB_USER\""
        echo "  export SAM_STAGING_DBPWWORD=\"$DBPW\""
        echo ""
        echo "=== For .env file (switch_to_staging_db.sh) ==="
        echo ""
        echo "  SAM_DB_SERVER=$DB_HOST"
        echo "  SAM_DB_USERNAME=$DB_USER"
        echo "  SAM_DB_PASSWORD=$DBPW"
        echo "  SAM_DB_REQUIRE_SSL=false"
        echo ""
        echo "=== Third-Party Tools (DBeaver, TablePlus, etc.) ==="
        echo ""
        echo "  Connection Type: MySQL"
        echo "  Host:            $DB_HOST"
        echo "  Port:            $DB_PORT"
        echo "  Database:        $DB_NAME"
        echo "  Username:        $DB_USER"
        echo "  Password:        (see above)"
        echo "  SSL:             Not required"
        echo "  Requires:        UCAR VPN connection"
        echo ""
        ;;
esac
