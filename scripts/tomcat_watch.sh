#!/bin/bash
# tomcat_watch.sh: READ-ONLY watch tick for legacy SAM (Tomcat on sam-tomcat, behind the
# sam.ucar.edu VIP): is it healthy, who still calls its API, and is that traffic falling as
# surfaces move to SAMuel. One ssh call reads the Tomcat logs on the host
# (lib/tomcat_log_summary.sh); lib/tomcat_report.py turns the records into lines.
# The watch-tomcat skill carries the thresholds and how to read each line.
#
# Usage:
#   scripts/tomcat_watch.sh [options]
#
# Options:
#       --host HOST    ssh target (default: $TOMCAT_HOST or sam-tomcat.ucar.edu)
#       --span DUR     window length, e.g. 35m, 2h            (default: 35m)
#       --at   UTC     a window centered on a past time instead of the tick, e.g.
#                      2026-10-10T03:26Z (any of the last 30 days; no state written)
#       --daily DATE   the cutover line for one Mountain day (YYYYMMDD)
#       --backfill     seed the daily history from the retained logs (30 days)
#       --trend [N]    the per-family history table, last N days (default: 14)
#       --no-color     plain output (also NO_COLOR, or when stdout is not a terminal)
#   -h, --help         this text
#
# The tick also appends yesterday to the daily history on its first run of a day. Local
# state (never committed) under ${XDG_STATE_HOME:-~/.local/state}/sam-watch/: state-tomcat,
# known-open-tomcat (printed back), tomcat-callers (the caller map), tomcat-daily.tsv.
# SAM_LEGACY_PROBE_USER/PASS (env or the repo .env) enable the authenticated http probe.
# TOMCAT_HOST=local runs the reader here against TOMCAT_LOGS (tests).
# Exit: 0 quiet / 1 warn / 2 fail.
set -uo pipefail
_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/common.sh
source "${_LIBDIR}/common.sh"
repo_paths

HOST="${TOMCAT_HOST:-sam-tomcat.ucar.edu}"
SPAN=35m; AT=""; DAILY=""; BACKFILL=0; TREND=""
PROBE_URL="${SAM_LEGACY_URL:-https://sam.ucar.edu}"
PROBE_PATH=/api/protected/admin/ldapsync/groupTag
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --span) SPAN="$2"; shift 2 ;;
        --at) AT="$2"; shift 2 ;;
        --daily) DAILY="$2"; shift 2 ;;
        --backfill) BACKFILL=1; shift ;;
        --trend) TREND=14; [[ "${2:-}" =~ ^[0-9]+$ ]] && { TREND="$2"; shift; }; shift ;;
        --no-color) export USE_COLOR=0; shift ;;
        -h|--help) usage_from_header "$0"; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done
setup_colors

DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sam-watch"
mkdir -p "$DIR"
STATE="$DIR/state-tomcat"; TSV="$DIR/tomcat-daily.tsv"; MAP="$DIR/tomcat-callers"
REPORT=(python3 "${_LIBDIR}/tomcat_report.py")

if [[ -n "$TREND" ]]; then "${REPORT[@]}" trend --tsv "$TSV" --days "$TREND"; exit 0; fi

# The reader always runs remotely through one ssh call, or locally for tests.
reader() {
    if [[ "$HOST" == local ]]; then bash "${_LIBDIR}/tomcat_log_summary.sh" "$@"
    else
        ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 \
            -o LogLevel=error "$HOST" bash -s -- "$@" < "${_LIBDIR}/tomcat_log_summary.sh"
    fi
}

if [[ "$HOST" != local ]] && ! ssh -o BatchMode=yes -o ConnectTimeout=8 -o LogLevel=error "$HOST" true 2>/dev/null; then
    echo "OFFLINE: ssh $HOST failed (VPN down, or no ssh master: run 'ssh $HOST true' once with Duo) — skipping this tick."
    exit 0
fi

if [[ "$BACKFILL" -eq 1 || -n "$DAILY" ]]; then
    if [[ "$BACKFILL" -eq 1 ]]; then DAYS=$("${REPORT[@]}" days --tsv "$TSV" --backfill 30); MODE=backfill
    else DAYS=$("${REPORT[@]}" days --tsv "$TSV" --date "$DAILY"); MODE=daily; fi
    # shellcheck disable=SC2086  # DAYS is a list of dates
    reader days $DAYS | "${REPORT[@]}" report --map "$MAP" --state "$STATE" --tsv "$TSV" --mode "$MODE" \
        ${DAILY:+--day "$DAILY"} | grep -v '^__verdict'
    exit 0
fi

NOW_ARG=(); [[ -n "${TOMCAT_NOW:-}" ]] && NOW_ARG=(--now "$TOMCAT_NOW")
eval "$("${REPORT[@]}" times --span "$SPAN" ${AT:+--at "$AT"} ${NOW_ARG[@]+"${NOW_ARG[@]}"})"
MODE='tick'; [[ -n "$AT" ]] && MODE='at'
TAG="window ${SPAN}"; [[ -n "$AT" ]] && TAG="window ${SPAN} centered on ${AT}"
echo "=== tick $(date -u '+%Y-%m-%d %H:%M:%SZ')  (${TAG})  [tomcat: ${HOST}] ==="

KNOWN_OPEN="$DIR/known-open-tomcat"
if [[ -f "$KNOWN_OPEN" ]]; then
    KNOWN_ITEMS=$(grep -vE '^\s*(#|$)' "$KNOWN_OPEN" || true)
    if [[ -n "$KNOWN_ITEMS" ]]; then
        echo "known-open ($(wc -l <<<"$KNOWN_ITEMS" | tr -d ' ') item(s), do not re-flag; edit $KNOWN_OPEN):"
        sed 's/^/  - /' <<<"$KNOWN_ITEMS"
    fi
fi

# The authenticated outside-in probe; its own hit is labeled `watch` by its user agent.
if [[ "$MODE" == tick && "$HOST" != local ]]; then
    PUSER="${SAM_LEGACY_PROBE_USER:-}"; PPASS="${SAM_LEGACY_PROBE_PASS:-}"
    for envf in "$REPO_ROOT/.env" "$REPO_ROOT/../.env"; do
        [[ -z "$PUSER" && -f "$envf" ]] || continue
        PUSER=$(sed -n 's/^SAM_LEGACY_PROBE_USER=//p' "$envf" | tail -1 | tr -d "\"'")
        PPASS=$(sed -n 's/^SAM_LEGACY_PROBE_PASS=//p' "$envf" | tail -1 | tr -d "\"'")
    done
    if [[ -z "$PUSER" ]]; then
        echo "http: skipped (no SAM_LEGACY_PROBE_USER)"
    else
        read -r CODE SECS < <(curl -s -o /dev/null --max-time 15 -A sam-tomcat-watch/1 \
            -w '%{http_code} %{time_total}\n' -K - "${PROBE_URL}${PROBE_PATH}" <<<"user = \"${PUSER}:${PPASS}\"")
        echo "http: GET ${PROBE_PATH} ${CODE:-000} in ${SECS:-?}s"
        if [[ "${CODE:-000}" == 000 || "${CODE:-000}" == 5* ]]; then fail "legacy http probe answered ${CODE:-000}"
        elif [[ "${CODE}" != 200 ]]; then warn "legacy http probe answered ${CODE}"
        elif awk -v s="${SECS:-0}" 'BEGIN { exit !(s > 4) }'; then warn "legacy http probe took ${SECS}s"; fi
    fi
fi

DAYS=""; [[ "$MODE" == tick ]] && DAYS=$("${REPORT[@]}" days --tsv "$TSV" ${NOW_ARG[@]+"${NOW_ARG[@]}"})
OUT=$(reader window "$FROM" "$TO" "$BFROM" "$BTO" ${DAYS:+days $DAYS} \
      | "${REPORT[@]}" report --map "$MAP" --state "$STATE" --tsv "$TSV" --mode "$MODE" --to "$TO")
if [[ -z "$OUT" || "$OUT" != *__verdict* ]]; then
    fail "the log reader returned nothing from $HOST"
else
    grep -v '^__verdict' <<<"$OUT"
    read -r _ W F < <(grep '^__verdict' <<<"$OUT")
    WARN_COUNT=$((WARN_COUNT + W)); FAIL_COUNT=$((FAIL_COUNT + F))
fi

if [[ $FAIL_COUNT -gt 0 || $WARN_COUNT -gt 0 ]]; then
    echo "  (${WARN_COUNT} warn, ${FAIL_COUNT} fail this tick)"
fi
if   [[ $FAIL_COUNT -gt 0 ]]; then exit 2
elif [[ $WARN_COUNT -gt 0 ]]; then exit 1
else exit 0
fi
