#!/usr/bin/env bash
#
# Drive the annual-report pipeline:
#   1. Run usage_q0_allocation_types.sql -> prints distinct allocation_types
#      seen in the period. If allocation_type_buckets.csv has no real rows
#      yet, this output is what you use to fill it in. Pass --skip-q0 once
#      the buckets file is populated.
#   2. Run usage_q5/q6/q7 with @period_grouping='lump' over [--start..--end].
#   3. Convert each TSV -> CSV via tsv_to_csv.py.
#   4. Invoke build_annual_report.py to assemble the final CSV.
#
# Credentials: relies on standard mysql client resolution (~/.my.cnf etc).
#
# Usage:
#   ./build_annual_report.sh --start 2024-10-01 --end 2025-08-14 [--out DIR]
#
set -euo pipefail

HOST="sam-sql.ucar.edu"
USER_OPT=""
DB="sam"
START=""
END=""
OUT=""
KEEP_TSV=0
RUN_Q0=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)     START="$2"; shift 2 ;;
        --end)       END="$2"; shift 2 ;;
        --host)      HOST="$2"; shift 2 ;;
        --user)      USER_OPT="$2"; shift 2 ;;
        --db)        DB="$2"; shift 2 ;;
        --out)       OUT="$2"; shift 2 ;;
        --skip-q0)   RUN_Q0=0; shift ;;
        --keep-tsv)  KEEP_TSV=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$START" || -z "$END" ]]; then
    echo "ERROR: --start and --end are required (YYYY-MM-DD)" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER="$SCRIPT_DIR/tsv_to_csv.py"
COMBINER="$SCRIPT_DIR/build_annual_report.py"

if [[ -z "$OUT" ]]; then
    OUT="$SCRIPT_DIR/../../data/annual_report_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUT"
echo ">>> Output directory: $OUT"

mysql_args=(--batch --host="$HOST" "$DB")
if [[ -n "$USER_OPT" ]]; then
    mysql_args=(--batch --host="$HOST" --user="$USER_OPT" "$DB")
fi

run_query() {
    local qfile="$1"; local outbase="$2"
    local tsv="$OUT/$outbase.tsv"
    local csv="$OUT/$outbase.csv"
    echo ">>> $qfile -> $outbase  ($START .. $END)"
    {
        printf "SET @start_date='%s';\n"      "$START"
        printf "SET @end_date='%s';\n"        "$END"
        printf "SET @period_grouping='lump';\n"
        cat "$SCRIPT_DIR/$qfile"
    } | mysql "${mysql_args[@]}" > "$tsv"
    sed -i.bak \
        -e 's/\tNULL\t/\t\t/g; s/\tNULL\t/\t\t/g' \
        -e 's/\tNULL$//; s/^NULL\t/\t/' \
        "$tsv"
    rm -f "$tsv.bak"
    python3 "$CONVERTER" "$tsv" >/dev/null
    [[ $KEEP_TSV -eq 0 ]] && rm -f "$tsv"
}

# ---- Q0 (optional) ----
if [[ $RUN_Q0 -eq 1 ]]; then
    run_query "usage_q0_allocation_types.sql" "usage_q0_allocation_types__lump"
    echo
    echo "=== Distinct allocation_type values for [$START..$END] ==="
    column -t -s, "$OUT/usage_q0_allocation_types__lump.csv" | head -50 || true
    echo
    echo "Confirm $SCRIPT_DIR/allocation_type_buckets.csv covers every row above"
    echo "before continuing. Press Enter to proceed, or Ctrl-C to stop and edit it."
    read -r _
fi

# ---- Q5 / Q6 / Q7 ----
run_query "usage_q5_projects_with_nsf.sql"        "usage_q5_projects_with_nsf__lump"
run_query "usage_q6_compute_by_project_machine.sql" "usage_q6_compute_by_project_machine__lump"
run_query "usage_q7_disk_by_project_resource.sql" "usage_q7_disk_by_project_resource__lump"

# ---- combiner ----
REPORT="$OUT/annual_report.csv"
python3 "$COMBINER" \
    --in-dir "$OUT" \
    --out    "$REPORT" \
    --start  "$START" \
    --end    "$END" \
    --maps   "$SCRIPT_DIR"

echo
echo ">>> Done."
echo ">>> Inputs:  $OUT/usage_q*__lump.csv"
echo ">>> Report:  $REPORT"
