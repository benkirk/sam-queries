#!/bin/bash
# Download the actual backup file from Git LFS

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/prereqs.sh"

# --- Prerequisites ---
require_cmd git git git "Git"

if ! git lfs version &>/dev/null; then
    echo "ERROR: Git LFS is not installed."
    echo ""
    if [ "$_PREREQS_OS" = "Darwin" ] && command -v brew &>/dev/null; then
        echo "  Installing via Homebrew: brew install git-lfs"
        brew install git-lfs && git lfs install
    else
        echo "  macOS:  brew install git-lfs && git lfs install"
        echo "  Linux:  sudo apt-get install git-lfs && git lfs install"
        exit 2
    fi
fi

echo "Downloading SAM database backup from Git LFS..."
echo ""

cd "$SCRIPT_DIR"

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
