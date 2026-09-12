#!/bin/bash
#
# SAM Database Anonymization - Complete Workflow
#
# 1. Preview transformations  2. Dry-run  3. Execute  4. Verify
# Pass --yes to skip the confirmation prompts (what `make bootstrap` does).
# Exits non-zero if anonymization, the username leak check, or the username
# consistency test fails. verify_anonymization.py is advisory: its heuristics
# ("common surnames", "NSF-like contract numbers") fire on every honest run.
#

set -e  # Exit on error

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0
ANON_FLAGS=()
if [ "${1:-}" = "--yes" ]; then
    YES=1
    ANON_FLAGS=(--yes)
fi

confirm() {  # confirm <prompt> <exit-code-on-no>
    [ $YES -eq 1 ] && return 0
    read -p "$1 (yes/no): " answer
    if [ "$answer" != "yes" ]; then
        echo "Aborted."
        exit "$2"
    fi
}

echo "================================================================================"
echo "SAM Database Anonymization Workflow"
echo "================================================================================"
echo ""

echo -e "${YELLOW}⚠️  IMPORTANT: This should ONLY be run on a database replica!${NC}"
echo -e "${YELLOW}   Never run this on a production database.${NC}"
echo ""
confirm "Are you running this on a REPLICA database?" 1

echo ""
echo "================================================================================"
echo "Step 1: Preview Sample Transformations"
echo "================================================================================"
echo ""
python3 "$SCRIPT_DIR/preview_anonymization.py"

echo ""
echo "================================================================================"
echo "Step 2: Dry-Run (Preview Changes)"
echo "================================================================================"
echo ""
python3 "$SCRIPT_DIR/anonymize_sam_db.py" --config config.yaml --dry-run

echo ""
echo "================================================================================"
echo "Step 3: Execute Anonymization"
echo "================================================================================"
echo ""
echo -e "${RED}⚠️  WARNING: This will PERMANENTLY modify the database!${NC}"
echo ""
confirm "Do you want to proceed with anonymization?" 0

echo ""
echo "Executing anonymization with mappings export..."
if python3 "$SCRIPT_DIR/anonymize_sam_db.py" --config config.yaml "${ANON_FLAGS[@]}" \
        --export-mappings "$SCRIPT_DIR/anonymization_mappings.json"; then
    echo ""
    echo -e "${GREEN}✓ Anonymization completed successfully!${NC}"
    echo -e "${GREEN}  Mappings saved to: $SCRIPT_DIR/anonymization_mappings.json${NC}"
else
    echo ""
    echo -e "${RED}✗ Anonymization failed!${NC}"
    exit 1
fi

echo ""
echo "================================================================================"
echo "Step 4: Verify Anonymization"
echo "================================================================================"
echo ""
set +e  # each check reports; the verdict is below

echo "Running heuristic checks (advisory)..."
python3 "$SCRIPT_DIR/verify_anonymization.py"
VERIFY_EXIT=$?

echo ""
echo "-------------------------------------------------------------------------------"
echo "Checking every username column for leaks..."
echo "-------------------------------------------------------------------------------"
python3 "$SCRIPT_DIR/check_username_leak.py" --config config.yaml
LEAK_EXIT=$?

echo ""
echo "-------------------------------------------------------------------------------"
echo "Testing username consistency across all tables..."
echo "-------------------------------------------------------------------------------"
python3 "$SCRIPT_DIR/test_username_anonymization.py"
CONSISTENCY_EXIT=$?

status() { [ "$1" -eq 0 ] && echo "  ✓ $2 passed" || echo "  ✗ $2 failed"; }

echo ""
[ $VERIFY_EXIT -eq 0 ] || echo -e "${YELLOW}  ⚠ heuristic checks raised warnings (advisory, see above)${NC}"
if [ $LEAK_EXIT -eq 0 ] && [ $CONSISTENCY_EXIT -eq 0 ]; then
    echo -e "${GREEN}SUCCESS: Database anonymization complete and verified!${NC}"
    status $LEAK_EXIT "Username leak check"
    status $CONSISTENCY_EXIT "Consistency check"
    exit 0
fi

echo -e "${RED}FAIL: verification detected leaks — do NOT dump or share this database${NC}"
status $LEAK_EXIT "Username leak check"
status $CONSISTENCY_EXIT "Consistency check"
exit 1
