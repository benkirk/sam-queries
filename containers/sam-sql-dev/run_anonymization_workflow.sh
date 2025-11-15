#!/bin/bash
#
# SAM Database Anonymization - Complete Workflow
#
# This script guides you through the complete anonymization process:
# 1. Preview transformations
# 2. Dry-run
# 3. Execute anonymization
# 4. Verify results
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "================================================================================"
echo "SAM Database Anonymization Workflow"
echo "================================================================================"
echo ""

# Check if running on a replica
echo -e "${YELLOW}⚠️  IMPORTANT: This should ONLY be run on a database replica!${NC}"
echo -e "${YELLOW}   Never run this on a production database.${NC}"
echo ""
read -p "Are you running this on a REPLICA database? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted. Please create a replica first."
    exit 1
fi

echo ""
echo "================================================================================"
echo "Step 1: Preview Sample Transformations"
echo "================================================================================"
echo ""
read -p "Press ENTER to see sample anonymization transformations..."
echo ""
python3 "$SCRIPT_DIR/preview_anonymization.py"

echo ""
echo ""
echo "================================================================================"
echo "Step 2: Dry-Run (Preview Changes)"
echo "================================================================================"
echo ""
read -p "Press ENTER to run dry-run (no changes will be committed)..."
echo ""
python3 "$SCRIPT_DIR/anonymize_sam_db.py" --config config.yaml --dry-run

echo ""
echo ""
echo "================================================================================"
echo "Step 3: Execute Anonymization"
echo "================================================================================"
echo ""
echo -e "${RED}⚠️  WARNING: This will PERMANENTLY modify the database!${NC}"
echo ""
read -p "Do you want to proceed with anonymization? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Executing anonymization with mappings export..."
python3 "$SCRIPT_DIR/anonymize_sam_db.py" --config config.yaml --export-mappings "$SCRIPT_DIR/../anonymization_mappings.json"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Anonymization completed successfully!${NC}"
    echo -e "${GREEN}  Mappings saved to: $SCRIPT_DIR/../anonymization_mappings.json${NC}"
else
    echo ""
    echo -e "${RED}✗ Anonymization failed!${NC}"
    exit 1
fi

echo ""
echo ""
echo "================================================================================"
echo "Step 4: Verify Anonymization"
echo "================================================================================"
echo ""
read -p "Press ENTER to verify anonymization results..."
echo ""
python3 "$SCRIPT_DIR/verify_anonymization.py"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}================================================================================${NC}"
    echo -e "${GREEN}SUCCESS: Database anonymization complete and verified!${NC}"
    echo -e "${GREEN}================================================================================${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Test your applications with the anonymized database"
    echo "  2. Review mappings file: anonymization_mappings.json"
    echo "  3. Verify foreign key relationships"
    echo ""
else
    echo ""
    echo -e "${YELLOW}⚠️  Verification detected some potential issues.${NC}"
    echo -e "${YELLOW}   Review the warnings above and investigate.${NC}"
    echo ""
fi

echo "Done!"
