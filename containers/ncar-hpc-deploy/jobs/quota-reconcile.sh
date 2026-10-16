#!/bin/bash
# Report-only quota reconcile after the disk ingest: SAM disk allocations against filesystem
# quotas, one JSON report per source in $NHD_STATE/quota-reconcile/ plus <R>.<key>.latest.json.
# A source reruns only when its file is newer than its latest report. Writes are blocked three
# ways: no argument but --dry-run, JSON mode refuses the write flags, and with no
# env.quota-reconcile overlay the job runs on the lane's read-only database role.
# Reports older than QUOTA_REPORT_DAYS (90) are pruned. Quasar has no reconcile reader.
# Usage: quota-reconcile.sh [--dry-run]
USAGE_DIR=${USAGE_DIR:-/glade/u/hdig/project_user_usage}
QUOTA_JSON=${QUOTA_JSON:-/glade/u/hdig/gladequota/cs_usage.json}
OUT="${NHD_STATE:-.}/quota-reconcile"
TIMEFORMAT='(%3R seconds elapsed)'
export COLUMNS=1024

dry=0
for a in "$@"; do
    case "${a}" in
        --dry-run) dry=1 ;;
        *) echo "ERROR: quota-reconcile is report-only; refusing argument '${a}'" >&2; exit 2 ;;
    esac
done
work="${OUT}"
(( dry )) && work="${TMPDIR:-/tmp}"
mkdir -p "${work}" || exit 2

# summarize NEW [OLD]: each category count, with its change since OLD when it moved.
summarize() {
    python3 - "$@" <<'PY'
import json, sys
new = json.load(open(sys.argv[1]))['counts']
try:
    old = json.load(open(sys.argv[2]))['counts'] if len(sys.argv) > 2 else {}
except (OSError, ValueError, KeyError):
    old = {}
print(' '.join(f"{k}={v}" + (f"({v - old[k]:+d})" if old.get(k, v) != v else '')
               for k, v in new.items()))
PY
}

rc=0
while read -r key resource kind mount; do
    if [[ "${kind}" == json ]]; then
        src=${QUOTA_JSON}
    else
        src=$(ls -t "${USAGE_DIR}"/acct."${key}".????-??-?? 2>/dev/null | head -1)
    fi
    tag="${resource}.${key}"
    latest="${OUT}/${tag}.latest.json"
    if [[ ! -r "${src}" ]]; then
        echo "${tag}: no readable source; skipping"; continue
    fi
    if [[ -e "${latest}" && ! "${src}" -nt "${latest}" ]]; then
        echo "${tag}: ${src##*/} already reported"; continue
    fi
    verify=()
    [[ -d "${mount}" ]] && verify=(--verify-paths)
    tmp=$(mktemp "${work}/.${tag}.XXXXXX") || { rc=2; continue; }
    echo "# ${tag}: ${src} ${verify[*]}"
    time sam-admin --format json accounting --reconcile-quotas "${src}" \
            --resource "${resource}" "${verify[@]}" > "${tmp}" 2> "${tmp}.err"; s=$?
    cat "${tmp}.err"
    if (( s != 0 )); then
        # The acct files gain quota columns from 2026-10-17; an older file is not a failure.
        if grep -q "has no quota columns" "${tmp}.err"; then
            echo "${tag}: ${src##*/} predates the quota columns; skipped"
        else
            echo "${tag}: sam-admin exit=${s}"; rc=2
        fi
        rm -f "${tmp}" "${tmp}.err"; continue
    fi
    echo "${tag}: $(summarize "${tmp}" "${latest}")"
    rm -f "${tmp}.err"
    if (( dry )); then
        rm -f "${tmp}"; continue
    fi
    report="${tag}.$(date +%F).json"
    # mktemp makes 0600; reports are for every reader of the lane state, as its stamps are.
    chmod 644 "${tmp}" && mv -f "${tmp}" "${OUT}/${report}" && ln -sfn "${report}" "${latest}" \
        || rc=2
done <<'MAP'
glade-json Campaign_Store json /gpfs/csfs1
glade      Campaign_Store acct /gpfs/csfs1
desc1      Destor         acct /lustre/desc1
MAP

(( dry )) || find "${OUT}" -name '*.json' -type f -mtime +"${QUOTA_REPORT_DAYS:-90}" -delete
exit ${rc}
