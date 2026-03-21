#!/bin/bash
# Switch .env to use staging database (AWS RDS)
# Requires UCAR VPN connection for database access.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/prereqs.sh"

ENV_FILE=".env"
STAGING_HOST="sam-staging-mysql.c0ntwnweue47.us-east-1.rds.amazonaws.com"
STAGING_USER="samadmin"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found!"
    exit 1
fi

# --- Prerequisites ---
check_vpn "$STAGING_HOST" 3306

echo "Switching to staging database..."
echo ""

# Backup current .env
cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "Backed up current .env file"

# Check for password
STAGING_PASS="${SAM_STAGING_DB_PASSWORD:-}"
if [ -z "$STAGING_PASS" ]; then
    read -s -p "Staging DB password (ask team lead or run db-creds-staging.sh): " STAGING_PASS
    echo
fi

if [ -z "$STAGING_PASS" ]; then
    echo "ERROR: Password is required."
    exit 1
fi

# Comment out local settings
sed -i.bak 's/^SAM_DB_USERNAME=root/#SAM_DB_USERNAME=root/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_SERVER=127.0.0.1/#SAM_DB_SERVER=127.0.0.1/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_PASSWORD=root/#SAM_DB_PASSWORD=root/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_REQUIRE_SSL=false/#SAM_DB_REQUIRE_SSL=false/' "$ENV_FILE"

# Comment out production settings if active
sed -i.bak 's/^SAM_DB_USERNAME=\${PROD_SAM_DB_USERNAME}/#SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_SERVER=\${PROD_SAM_DB_SERVER}/#SAM_DB_SERVER=${PROD_SAM_DB_SERVER}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_PASSWORD=\${PROD_SAM_DB_PASSWORD}/#SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_REQUIRE_SSL=true/#SAM_DB_REQUIRE_SSL=true/' "$ENV_FILE"

# Comment out any existing staging settings
sed -i.bak '/^SAM_DB_.*# staging$/d' "$ENV_FILE"

# Add staging settings before the status DB section
if grep -q "^#.*system_status" "$ENV_FILE"; then
    sed -i.bak "/^#.*system_status/i\\
SAM_DB_USERNAME=$STAGING_USER  # staging\\
SAM_DB_SERVER=$STAGING_HOST  # staging\\
SAM_DB_PASSWORD=$STAGING_PASS  # staging\\
SAM_DB_REQUIRE_SSL=false  # staging\\
" "$ENV_FILE"
else
    cat >> "$ENV_FILE" <<EOF

SAM_DB_USERNAME=$STAGING_USER  # staging
SAM_DB_SERVER=$STAGING_HOST  # staging
SAM_DB_PASSWORD=$STAGING_PASS  # staging
SAM_DB_REQUIRE_SSL=false  # staging
EOF
fi

# Remove backup files created by sed
rm -f "${ENV_FILE}.bak"

echo "Switched to staging database"
echo ""
echo "Current settings:"
grep "^SAM_DB_" "$ENV_FILE" | grep -v "^#"
echo ""
echo "NOTE: Requires UCAR VPN connection"
echo "   - sam-search, sam-admin, and Python tools will use staging"
echo "   - Webapp CRUD operations will work (read/write access)"
echo ""
echo "To switch back to local: ./scripts/setup/switch_to_local_db.sh"
echo "To switch to production: ./scripts/setup/switch_to_production_db.sh"
