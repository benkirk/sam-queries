#!/bin/bash
#
# export_project_users.sh
#
# Generates a CSV file containing all users from a project and its active sub-projects.
#
# Usage: ./export_project_users.sh <project_code> [output_file.csv]
#
# Example: ./export_project_users.sh NMMM0003 nmmm0003_users.csv
#

set -euo pipefail

# Set wide column width to prevent email truncation
export COLUMNS=200

# Check arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 <project_code> [output_file.csv]" >&2
    echo "Example: $0 NMMM0003 nmmm0003_users.csv" >&2
    exit 1
fi

PROJECT_CODE="$1"
OUTPUT_FILE="${2:-${PROJECT_CODE}_users.csv}"

# Temporary file for storing project hierarchy
TEMP_HIERARCHY=$(mktemp)
TEMP_PROJECTS=$(mktemp)

# Cleanup on exit
trap "rm -f $TEMP_HIERARCHY $TEMP_PROJECTS" EXIT

echo "Fetching project hierarchy for $PROJECT_CODE..." >&2

# Get project info with verbose flag to get hierarchy
if ! sam-search project "$PROJECT_CODE" --verbose > "$TEMP_HIERARCHY" 2>&1; then
    echo "Error: Could not fetch project information for $PROJECT_CODE" >&2
    cat "$TEMP_HIERARCHY" >&2
    exit 1
fi

echo "Extracting active project codes from hierarchy..." >&2

# Extract all project codes from the hierarchy section
# - Look for lines in the hierarchy box (starting with │)
# - Extract project codes (pattern: uppercase letters followed by digits)
# - Exclude lines marked as "(Inactive)"
# - Remove duplicates
grep "^│" "$TEMP_HIERARCHY" | \
    grep -v "(Inactive)" | \
    grep -oE '[A-Z][A-Z0-9]+[0-9]{4,}' | \
    sort -u > "$TEMP_PROJECTS"

# Count projects found
PROJECT_COUNT=$(wc -l < "$TEMP_PROJECTS" | tr -d ' ')
echo "Found $PROJECT_COUNT active projects (including parent)" >&2

# Create CSV header
echo "project_code,username,full_name,email" > "$OUTPUT_FILE"

# Process each project
TOTAL_USERS=0
while IFS= read -r projcode; do
    echo "Processing $projcode..." >&2

    # Get user list for this project
    # Use --list-users --verbose to get the user table with email column
    # Temporarily disable pipefail for the command substitution
    set +e
    USER_OUTPUT=$(COLUMNS=200 sam-search project "$projcode" --list-users --verbose 2>&1)
    SEARCH_EXIT=$?
    set -e

    if [ $SEARCH_EXIT -ne 0 ]; then
        echo "  Warning: Could not fetch users for $projcode (exit code: $SEARCH_EXIT), skipping..." >&2
        continue
    fi

    # Check if there are active users
    if echo "$USER_OUTPUT" | grep -q "No active users"; then
        echo "  No active users found" >&2
        continue
    fi

    # Extract user information from the table
    # The table has columns: #, Username, Name, Access Notes, Email, UID
    # Data rows start with a number followed by whitespace
    # We need to extract: username, name (may have spaces), email

    USER_COUNT=0
    while IFS= read -r line; do
        # Skip empty lines, header lines, and separator lines
        # Look for lines that start with whitespace, then ONLY digits, then whitespace
        if [[ ! "$line" =~ ^[[:space:]]+[0-9]+[[:space:]]+[a-zA-Z] ]]; then
            continue
        fi

        # Parse the line - it's space-delimited with possible multi-word names
        # Format with COLUMNS=200: "  1      robynt     Robyn Tye      no access to Data_Access   robynt@ucar.edu     40905"

        # Extract username (2nd field)
        username=$(echo "$line" | awk '{print $2}')

        # Extract email using regex (||true to handle no match without exiting)
        email=$(echo "$line" | grep -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' || true)

        # Extract name - everything between username and "no access" or email pattern
        # Remove leading number and username first (use gsed for better regex support)
        rest=$(echo "$line" | gsed -E 's/^[[:space:]]*[0-9]+[[:space:]]+//' | gsed -E "s/^${username}[[:space:]]+//")

        # Now extract just the name part (before "no access" or email)
        name=$(echo "$rest" | gsed -E 's/[[:space:]]+no access.*$//' | gsed -E 's/[[:space:]]+[a-zA-Z0-9._%+-]+@.*$//' | gsed -E 's/[[:space:]]*$//')

        # If we couldn't extract email or username, skip this line
        if [ -z "$email" ] || [ -z "$username" ]; then
            continue
        fi

        # Output CSV row (quote fields to handle commas)
        echo "$projcode,$username,\"$name\",$email" >> "$OUTPUT_FILE"

        ((USER_COUNT++))
        ((TOTAL_USERS++))
    done <<< "$USER_OUTPUT"

    echo "  Found $USER_COUNT users" >&2

done < "$TEMP_PROJECTS"

echo "" >&2
echo "Export complete!" >&2
echo "Total projects processed: $PROJECT_COUNT" >&2
echo "Total users exported: $TOTAL_USERS" >&2
echo "Output file: $OUTPUT_FILE" >&2
