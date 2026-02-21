#!/bin/bash
# Switch .env to use production database

set -e

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env file not found!"
    exit 1
fi

echo "Switching to production database..."
echo ""

# Backup current .env
cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "✅ Backed up current .env file"

# Use sed to comment/uncomment the right sections
# Comment out local settings
sed -i.bak 's/^SAM_DB_USERNAME=root/#SAM_DB_USERNAME=root/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_SERVER=127.0.0.1/#SAM_DB_SERVER=127.0.0.1/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_PASSWORD=root/#SAM_DB_PASSWORD=root/' "$ENV_FILE"
sed -i.bak 's/^SAM_DB_REQUIRE_SSL=false/#SAM_DB_REQUIRE_SSL=false/' "$ENV_FILE"

# Uncomment production settings
sed -i.bak 's/^#SAM_DB_USERNAME=\${PROD_SAM_DB_USERNAME}/SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_SERVER=\${PROD_SAM_DB_SERVER}/SAM_DB_SERVER=${PROD_SAM_DB_SERVER}/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_PASSWORD=\${PROD_SAM_DB_PASSWORD}/SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}/' "$ENV_FILE"
sed -i.bak 's/^#SAM_DB_REQUIRE_SSL=true/SAM_DB_REQUIRE_SSL=true/' "$ENV_FILE"

# Remove backup files created by sed
rm -f "${ENV_FILE}.bak"

echo "✅ Switched to production database"
echo ""
echo "Current settings:"
grep "^SAM_DB_" "$ENV_FILE" | grep -v "^#"
echo ""
echo "⚠️  Note: Production database is READ-ONLY"
echo "   - CLI queries will work"
echo "   - Python REPL will work"
echo "   - Webapp CRUD operations will fail (read-only)"
echo ""
echo "To switch back to local: ./switch_to_local_db.sh"
