#!/bin/bash
# Switch .env to use local database

set -e

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env file not found!"
    exit 1
fi

echo "Switching to local database..."
echo ""

# Backup current .env
cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "✅ Backed up current .env file"

# Comment out production settings
sed -i.bak 's/^SAM_DB_USERNAME=\${PROD_SAM_DB_USERNAME}/#SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_SERVER=\${PROD_SAM_DB_SERVER}/#SAM_DB_SERVER=${PROD_SAM_DB_SERVER}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_PASSWORD=\${PROD_SAM_DB_PASSWORD}/#SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_REQUIRE_SSL=true/#SAM_DB_REQUIRE_SSL=true/' "$ENV_FILE"

# Uncomment local settings
sed -i.bak 's/^#SAM_DB_USERNAME=root/SAM_DB_USERNAME=root/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_SERVER=127.0.0.1/SAM_DB_SERVER=127.0.0.1/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_PASSWORD=root/SAM_DB_PASSWORD=root/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_REQUIRE_SSL=false/SAM_DB_REQUIRE_SSL=false/' "$ENV_FILE"

# Remove backup files created by sed
rm -f "${ENV_FILE}.bak"

echo "✅ Switched to local database"
echo ""
echo "Current settings:"
grep "^SAM_DB_" "$ENV_FILE" | grep -v "^#"
echo ""
echo "Note: Local database requires Docker container to be running"
echo "   Start with: docker compose up -d mysql"
echo ""
echo "To switch to production: ./switch_to_production_db.sh"
