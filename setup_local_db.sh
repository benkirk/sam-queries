#!/bin/bash
# Complete local database setup: download backup, start container, restore database

set -e

echo "=========================================="
echo "SAM Queries - Local Database Setup"
echo "=========================================="
echo ""

cd "$(dirname "$0")"

# Check Docker
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running!"
    echo ""
    echo "Please start Docker Desktop and run this script again."
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Check if backup file exists and is valid
BACKUP_FILE="containers/sam-sql-dev/backups/sam-obfuscated.sql.xz"
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    echo ""
    echo "Download it with: ./scripts/setup/download_backup.sh"
    exit 1
fi

# Check if backup is Git LFS pointer
if head -1 "$BACKUP_FILE" 2>/dev/null | grep -q "version https://git-lfs.github.com"; then
    echo "⚠️  Backup file is a Git LFS pointer"
    echo ""
    echo "Downloading actual backup file from Git LFS..."
    
    if ! command -v git-lfs &> /dev/null && ! git lfs version &> /dev/null; then
        echo "❌ Git LFS is not installed!"
        echo ""
        echo "Install it with:"
        echo "  brew install git-lfs"
        echo "  git lfs install"
        echo ""
        echo "Or download backup manually: ./scripts/setup/download_backup.sh"
        exit 1
    fi
    
    if ! git lfs pull --include="$BACKUP_FILE"; then
        echo "❌ Failed to download backup file"
        echo "Try manually: ./scripts/setup/download_backup.sh"
        exit 1
    fi
    
    echo "✅ Backup file downloaded"
    echo ""
fi

# Verify backup file is valid
if ! file "$BACKUP_FILE" | grep -q "XZ compressed"; then
    echo "⚠️  Backup file doesn't appear to be XZ compressed"
    echo "File type: $(file "$BACKUP_FILE")"
    echo ""
fi

# Check if container is already running
if docker compose ps mysql 2>/dev/null | grep -q "Up.*healthy"; then
    echo "✅ MySQL container is already running and healthy"
    echo ""
    echo "To restart with fresh data, run:"
    echo "  docker compose down mysql"
    echo "  docker volume rm sam-queries_samuel-mysql-data"
    echo "  ./setup_local_db.sh"
    exit 0
fi

# Stop and remove existing container/volume if needed
if docker compose ps mysql 2>/dev/null | grep -q "Up"; then
    echo "Stopping existing MySQL container..."
    docker compose stop mysql
fi

if docker compose ps -a mysql 2>/dev/null | grep -q "mysql"; then
    echo "Removing existing MySQL container..."
    docker compose rm -f mysql
fi

echo "Removing MySQL data volume (if exists)..."
docker volume rm sam-queries_samuel-mysql-data 2>/dev/null || true

echo ""
echo "Starting MySQL container (will restore from backup)..."
echo "This may take 5-10 minutes for large backups."
echo ""

docker compose up -d mysql

echo "Waiting for database restore to complete..."
echo ""

# Wait for health check (up to 10 minutes)
timeout=600
elapsed=0
while [ $elapsed -lt $timeout ]; do
    STATUS=$(docker compose ps mysql 2>/dev/null | grep mysql | awk '{print $6" "$7}' || echo "")
    
    # Show progress every 30 seconds
    if [ $((elapsed % 30)) -eq 0 ] && [ $elapsed -gt 0 ]; then
        echo "[$elapsed seconds] Status: $STATUS"
    fi
    
    if echo "$STATUS" | grep -q "healthy"; then
        echo ""
        echo "✅ Database restore completed!"
        echo ""
        
        # Verify database exists
        sleep 2
        if docker compose exec -T mysql mysql -uroot -proot -e "USE sam; SHOW TABLES;" 2>/dev/null | head -5 | grep -q "."; then
            TABLE_COUNT=$(docker compose exec -T mysql mysql -uroot -proot sam -e "SHOW TABLES;" 2>/dev/null | wc -l | tr -d ' ')
            echo "✅ Database 'sam' exists with $((TABLE_COUNT - 1)) tables"
            echo ""
            echo "🎉 Setup complete! Test with:"
            echo "  ./test_database.sh"
            echo ""
            echo "Or test CLI directly:"
            echo "  source etc/config_env.sh"
            echo "  sam-search user --search 'a%' | head -10"
        fi
        exit 0
    fi
    
    sleep 5
    elapsed=$((elapsed + 5))
done

echo ""
echo "⚠️  Timeout waiting for database to become healthy"
echo ""
echo "Check status: docker compose ps mysql"
echo "Check logs: docker compose logs mysql | tail -50"
echo ""
echo "The restore may still be in progress. Large backups can take 10+ minutes."
exit 1
