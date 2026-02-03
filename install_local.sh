#!/bin/bash
# Local installation helper script for SAM Queries

set -e

echo "=========================================="
echo "SAM Queries - Local Installation"
echo "=========================================="
echo ""

# Check for conda
if ! command -v conda &> /dev/null; then
    echo "❌ Conda is not installed."
    echo ""
    echo "Please install Miniconda first:"
    echo ""
    echo "Option 1: Download and install manually"
    echo "  1. Visit: https://docs.conda.io/en/latest/miniconda.html"
    echo "  2. Download: Miniconda3 macOS Intel x86 64-bit (or Apple Silicon if M1/M2/M3)"
    echo "  3. Run: bash ~/Downloads/Miniconda3-latest-MacOSX-x86_64.sh"
    echo "  4. Restart terminal or run: source ~/.zshrc"
    echo ""
    echo "Option 2: Use Homebrew (if permissions fixed)"
    echo "  brew install --cask miniconda"
    echo ""
    echo "After installing Conda, run this script again."
    exit 1
fi

echo "✅ Conda found: $(conda --version)"
echo ""

# Initialize conda if needed
if [ -z "$CONDA_DEFAULT_ENV" ]; then
    echo "Initializing conda..."
    eval "$(conda shell.bash hook)"
fi

# Create conda environment
echo "Creating conda environment..."
cd "$(dirname "$0")"
make conda-env

echo ""
echo "✅ Conda environment created!"
echo ""
echo "Next steps:"
echo ""
echo "For local development with Docker database:"
echo "  1. Set up local database: ./setup_local_db.sh"
echo "  2. Test installation: ./test_database.sh"
echo ""
echo "For production database:"
echo "  1. Switch to production: ./scripts/setup/switch_to_production_db.sh"
echo "  2. Activate environment: source etc/config_env.sh"
echo "  3. Test: sam-search user --search 'a%' | head -10"
echo ""
echo "See docs/LOCAL_SETUP.md for complete setup guide."
echo "See scripts/setup/README.md for utility scripts."
echo ""
