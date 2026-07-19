#!/usr/bin/env bash
#
# NSF grant-funding rollup: how many NSF funding grants supported projects in a
# period, and their total awarded dollars (overall + per compute resource).
#
# SAM's DB has no award dollar amount, so amounts are pulled from the public NSF
# Awards API (api.nsf.gov) via nsf_awards.py and cached in nsf_award_lookups.csv.
#
# Two modes:
#   1. Full run  — run usage_q5 + usage_q6 for [--start..--end], convert to CSV,
#                  then summarize:
#        ./run_nsf_grant_funding.sh --start 2025-07-01 --end 2026-06-30
#   2. Reuse CSVs — summarize an existing build_annual_report.sh output dir
#                  (its usage_q5/q6 *__lump.csv are already present):
#        ./run_nsf_grant_funding.sh --in-dir ../../data/annual_report_XXXX \
#            --start 2025-07-01 --end 2026-06-30
#
# Credentials: standard mysql client resolution (~/.my.cnf, MYSQL_PWD, prompt).
# Do NOT put a password on the command line.
#
# Options:
#   --start / --end   YYYY-MM-DD (required; --end also labels the headline)
#   --in-dir DIR      reuse existing Q5/Q6 CSVs instead of querying mysql
#   --host / --user / --db   mysql connection (defaults: sam-sql.ucar.edu / sam)
#   --out DIR         output dir for a full run (default: data/nsf_grant_funding_<ts>)
#   --csv             also write a machine-readable summary CSV
#   --no-network      rely on the existing NSF cache only (no api.nsf.gov calls)
#   --keep-tsv        keep intermediate TSVs
set -euo pipefail

# ---- defaults --------------------------------------------------------------
HOST="sam-sql.ucar.edu"
USER_OPT=""
DB="sam"
START=""
END=""
OUT=""
IN_DIR=""
WRITE_CSV=0
NO_NETWORK=0
KEEP_TSV=0

# ---- arg parse -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)      START="$2"; shift 2 ;;
        --end)        END="$2"; shift 2 ;;
        --host)       HOST="$2"; shift 2 ;;
        --user)       USER_OPT="$2"; shift 2 ;;
        --db)         DB="$2"; shift 2 ;;
        --out)        OUT="$2"; shift 2 ;;
        --in-dir)     IN_DIR="$2"; shift 2 ;;
        --csv)        WRITE_CSV=1; shift ;;
        --no-network) NO_NETWORK=1; shift ;;
        --keep-tsv)   KEEP_TSV=1; shift ;;
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER="$SCRIPT_DIR/tsv_to_csv.py"
ROLLUP="$SCRIPT_DIR/nsf_grant_funding.py"

# ---- obtain Q5/Q6 CSVs -----------------------------------------------------
if [[ -n "$IN_DIR" ]]; then
    # Reuse existing CSVs (from a prior build_annual_report.sh run).
    DATA_DIR="$IN_DIR"
    for f in usage_q5_projects_with_nsf__lump.csv \
             usage_q6_compute_by_project_machine__lump.csv; do
        if [[ ! -f "$DATA_DIR/$f" ]]; then
            echo "ERROR: $DATA_DIR/$f not found (need a build_annual_report output dir)" >&2
            exit 2
        fi
    done
    echo ">>> Reusing Q5/Q6 CSVs in $DATA_DIR"
else
    if [[ -z "$OUT" ]]; then
        OUT="$SCRIPT_DIR/../../data/nsf_grant_funding_$(date +%Y%m%d_%H%M%S)"
    fi
    mkdir -p "$OUT"
    DATA_DIR="$OUT"
    echo ">>> Output directory: $OUT"

    mysql_args=(--batch --host="$HOST" "$DB")
    if [[ -n "$USER_OPT" ]]; then
        mysql_args=(--batch --host="$HOST" --user="$USER_OPT" "$DB")
    fi

    run_query() {
        local qfile="$1"; local outbase="$2"
        local tsv="$DATA_DIR/$outbase.tsv"
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

    run_query "usage_q5_projects_with_nsf.sql"          "usage_q5_projects_with_nsf__lump"
    run_query "usage_q6_compute_by_project_machine.sql" "usage_q6_compute_by_project_machine__lump"
fi

# ---- summarize -------------------------------------------------------------
rollup_args=(--in-dir "$DATA_DIR" --maps "$SCRIPT_DIR" --start "$START" --end "$END")
[[ $NO_NETWORK -eq 1 ]] && rollup_args+=(--no-network)
[[ $WRITE_CSV -eq 1 ]]  && rollup_args+=(--csv "$DATA_DIR/nsf_grant_funding.csv")

python3 "$ROLLUP" "${rollup_args[@]}"
