#!/bin/bash
# Fix MySQL permissions to allow connections from Docker Desktop VM

# Don't exit on error - we'll handle connection failures
set +e

echo "Fixing MySQL permissions for Docker Desktop connections..."
echo ""

# Check if container is running
if ! docker compose ps mysql 2>/dev/null | grep -q "Up"; then
    echo "❌ MySQL container is not running!"
    echo "Start it with: docker compose up -d mysql"
    exit 1
fi

echo "Checking MySQL connection methods..."
echo ""

# Try different connection methods
# Method 1: Try without password first (if initialized insecure)
if docker compose exec -T mysql mysql -uroot -e "SELECT 1" >/dev/null 2>&1; then
    echo "✅ Connected without password (insecure initialization detected)"
    PASSWORD_ARG=""
    NEEDS_PASSWORD_SET=true
# Method 2: Try with password 'root'
elif docker compose exec -T mysql mysql -uroot -proot -e "SELECT 1" >/dev/null 2>&1; then
    echo "✅ Connected with password 'root'"
    PASSWORD_ARG="-proot"
    NEEDS_PASSWORD_SET=false
else
    echo "❌ Cannot connect to MySQL with either method"
    echo "Container may not be ready yet. Check logs with: docker compose logs mysql"
    exit 1
fi

echo ""
echo "Setting up root user permissions..."

# If password wasn't set, set it first
if [ "$NEEDS_PASSWORD_SET" = "true" ]; then
    echo "Setting root password..."
    docker compose exec -T mysql mysql -uroot <<EOF
ALTER USER 'root'@'localhost' IDENTIFIED BY 'root';
FLUSH PRIVILEGES;
EOF
    PASSWORD_ARG="-proot"
fi

echo "Granting root access from any host..."
docker compose exec -T mysql mysql -uroot ${PASSWORD_ARG} <<EOF
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SELECT User, Host FROM mysql.user WHERE User='root';
EOF

echo ""
echo "✅ Permissions updated!"
echo ""
echo "You can now test the connection:"
echo "  source etc/config_env.sh"
echo "  sam-search user --search 'a%' | head -10"
