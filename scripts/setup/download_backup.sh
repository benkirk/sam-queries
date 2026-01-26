#!/bin/bash
# Download the actual backup file from Git LFS

echo "Downloading SAM database backup from Git LFS..."
echo ""

cd "$(dirname "$0")"

# Check if git-lfs is installed
if ! command -v git-lfs &> /dev/null && ! git lfs version &> /dev/null; then
    echo "❌ Git LFS is not installed!"
    echo ""
    echo "Install it with:"
    echo "  brew install git-lfs"
    echo "  git lfs install"
    echo ""
    exit 1
fi

echo "Pulling backup file from Git LFS..."
if git lfs pull --include="containers/sam-sql-dev/backups/sam-obfuscated.sql.xz"; then
    echo ""
    echo "✅ Backup file downloaded!"
    echo ""
    echo "File size:"
    ls -lh containers/sam-sql-dev/backups/sam-obfuscated.sql.xz
    echo ""
    echo "Now you can restore the database:"
    echo "  ./restore_database.sh"
else
    echo ""
    echo "❌ Failed to download backup file"
    echo ""
    echo "You may need to:"
    echo "  1. Ensure you have Git LFS access to this repository"
    echo "  2. Check your Git LFS credentials"
    echo "  3. Or use production database credentials instead"
    exit 1
fi
