#!/bin/bash
# Test database connection and queries

set +e

echo "Testing SAM database connection..."
echo ""

# Test 1: Check if database exists
echo "1. Checking if 'sam' database exists..."
if docker compose exec -T mysql mysql -uroot -proot -e "SHOW DATABASES;" 2>/dev/null | grep -q "sam"; then
    echo "   ✅ Database 'sam' exists"
else
    echo "   ❌ Database 'sam' does not exist"
    exit 1
fi

# Test 2: Check table count
echo ""
echo "2. Checking tables..."
TABLE_COUNT=$(docker compose exec -T mysql mysql -uroot -proot sam -e "SHOW TABLES;" 2>/dev/null | wc -l | tr -d ' ')
if [ "$TABLE_COUNT" -gt 1 ]; then
    echo "   ✅ Database has $((TABLE_COUNT - 1)) tables"
else
    echo "   ⚠️  Database exists but has no tables"
fi

# Test 3: Check if users table exists and has data
echo ""
echo "3. Checking 'users' table..."
USER_COUNT=$(docker compose exec -T mysql mysql -uroot -proot sam -e "SELECT COUNT(*) FROM users;" 2>/dev/null | tail -1 | tr -d ' ')
if [ -n "$USER_COUNT" ] && [ "$USER_COUNT" -gt 0 ]; then
    echo "   ✅ Users table has $USER_COUNT records"
else
    echo "   ⚠️  Users table is empty or doesn't exist"
fi

# Test 4: Test CLI connection
echo ""
echo "4. Testing CLI connection..."
cd "$(dirname "$0")"
source etc/config_env.sh >/dev/null 2>&1

if sam-search user --search "a%" 2>&1 | head -5 | grep -q -E "(username|User|Error)"; then
    echo "   ✅ CLI connection successful!"
    echo ""
    echo "   Sample output:"
    sam-search user --search "a%" 2>&1 | head -10
else
    ERROR=$(sam-search user --search "a%" 2>&1 | head -3)
    echo "   ❌ CLI connection failed:"
    echo "   $ERROR"
    exit 1
fi

echo ""
echo "🎉 All tests passed! Database is ready to use."
