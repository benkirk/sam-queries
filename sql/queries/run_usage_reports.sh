#!/usr/bin/env bash
#
# Run the usage_q{1..4}_*.sql reports for each period grouping
# (quarterly / annual / lump) over a given date range, emit TSV via
# `mysql --batch`, and convert to CSV with tsv_to_csv.py.
#
# Output: <OUT>/<query>__<grouping>.csv (e.g. usage_q1_unique_counts__annual.csv)
#
# Credentials: relies on standard mysql client resolution — `~/.my.cnf`,
# MYSQL_PWD env var, or interactive prompt. Do NOT put a password on
# the command line.
#
# Usage:
#   ./run_usage_reports.sh --start 2021-10-01 --end 2025-09-30 \
#       [--host sam-sql.ucar.edu] [--user $USER] [--db sam] \
#       [--out DIR] [--keep-tsv]
#
set -euo pipefail

# ---- defaults --------------------------------------------------------------
HOST="sam-sql.ucar.edu"
USER_OPT=""              # mysql will fall back to $USER if empty
DB="sam"
START=""
END=""
OUT=""
KEEP_TSV=0

# ---- arg parse -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)    START="$2"; shift 2 ;;
        --end)      END="$2"; shift 2 ;;
        --host)     HOST="$2"; shift 2 ;;
        --user)     USER_OPT="$2"; shift 2 ;;
        --db)       DB="$2"; shift 2 ;;
        --out)      OUT="$2"; shift 2 ;;
        --keep-tsv) KEEP_TSV=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$START" || -z "$END" ]]; then
    echo "ERROR: --start YYYY-MM-DD and --end YYYY-MM-DD are required" >&2
    exit 2
fi

# Resolve directory of this script so we can find the .sql files and the
# tsv_to_csv.py converter regardless of where the user runs from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER="$SCRIPT_DIR/tsv_to_csv.py"

if [[ -z "$OUT" ]]; then
    OUT="$SCRIPT_DIR/../../data/usage_reports_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUT"
echo ">>> Output directory: $OUT"

# ---- queries + groupings ---------------------------------------------------
QUERIES=(
    "usage_q1_unique_counts.sql"
    "usage_q2_by_facility.sql"
    "usage_q3_by_institution.sql"
    "usage_q4_by_alloc_type.sql"
)
GROUPINGS=(quarterly annual lump)

mysql_args=(--batch --host="$HOST" "$DB")
if [[ -n "$USER_OPT" ]]; then
    mysql_args=(--batch --host="$HOST" --user="$USER_OPT" "$DB")
fi

# ---- main loop -------------------------------------------------------------
for q in "${QUERIES[@]}"; do
    qpath="$SCRIPT_DIR/$q"
    if [[ ! -f "$qpath" ]]; then
        echo "  skip: $q (not found)" >&2
        continue
    fi
    for g in "${GROUPINGS[@]}"; do
        base="${q%.sql}__${g}"
        tsv="$OUT/$base.tsv"
        csv="$OUT/$base.csv"
        echo ">>> $q  [$g]  ($START .. $END)"

        # Prepend SET statements so the .sql file does not need editing.
        {
            printf "SET @start_date='%s';\n"      "$START"
            printf "SET @end_date='%s';\n"        "$END"
            printf "SET @period_grouping='%s';\n" "$g"
            cat "$qpath"
        } | mysql "${mysql_args[@]}" > "$tsv"

        # Replace mysql's literal "NULL" tokens with empty fields before CSV
        # conversion (cosmetic — spreadsheets read empty as blank, not "NULL").
        # Only touches whole-field NULLs (tab-NULL-tab, leading, trailing).
        sed -i.bak \
            -e 's/\tNULL\t/\t\t/g; s/\tNULL\t/\t\t/g' \
            -e 's/\tNULL$//; s/^NULL\t/\t/' \
            "$tsv"
        rm -f "$tsv.bak"

        python3 "$CONVERTER" "$tsv" >/dev/null
        [[ $KEEP_TSV -eq 0 ]] && rm -f "$tsv"
    done
done

# ---- NOTES.md --------------------------------------------------------------
cat > "$OUT/NOTES.md" <<EOF
# Usage reports — $(date)

Date range: $START .. $END
Host:       $HOST
Database:   $DB

## Files

Each (question x grouping) combination produces one CSV:

|                              | quarterly | annual | lump (single bucket) |
| ---------------------------- | --------- | ------ | -------------------- |
| Q1 unique counts             | usage_q1_unique_counts__quarterly.csv | usage_q1_unique_counts__annual.csv | usage_q1_unique_counts__lump.csv |
| Q2 by facility               | usage_q2_by_facility__quarterly.csv | usage_q2_by_facility__annual.csv | usage_q2_by_facility__lump.csv |
| Q3 by institution            | usage_q3_by_institution__quarterly.csv | usage_q3_by_institution__annual.csv | usage_q3_by_institution__lump.csv |
| Q4 by allocation_type        | usage_q4_by_alloc_type__quarterly.csv | usage_q4_by_alloc_type__annual.csv | usage_q4_by_alloc_type__lump.csv |

## Source tables

All four reports UNION ALL three tables:
\`hpc_charge_summary\`, \`dav_charge_summary\`, \`comp_charge_summary\`.

\`core_hours\` and \`charges\` are raw values from the summary tables
(no machine_factor multiplier applied).

## Caveat — Q3 multi-institution attribution

When a user is affiliated with N institutions in \`user_institution\`,
\`usage_q3_by_institution\` credits the user's hours to EACH of those N
institutions (the row is multiplied by the LEFT JOIN). This matches
existing NCAR reporting practice (\`hpc_usage_totals.sql\`) but means:

  SUM of Q3.total_core_hours over all institutions != Q1.total_core_hours

If a strictly partitioned attribution is needed instead, restrict the
join to a primary-affiliation row in \`user_institution\` (if such a flag
exists) before aggregating.
EOF

echo ">>> Done. CSVs in $OUT"
ls -1 "$OUT"/*.csv
