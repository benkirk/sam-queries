#!/bin/bash
# Disk charge summaries from the newest hdig acct.<fs>.YYYY-MM-DD file per resource.
# A resource reruns only when its newest file is newer than its stamp in $NHD_STATE.
# Usage: accounting-disk.sh [extra sam-admin args, e.g. --dry-run]
USAGE_DIR=${USAGE_DIR:-/glade/u/hdig/project_user_usage}
TIMEFORMAT='(%3R seconds elapsed)'
export COLUMNS=1024

rc=0
while read -r key resource; do
    latest=$(ls -t "${USAGE_DIR}"/acct."${key}".????-??-?? 2>/dev/null | head -1)
    stamp="${NHD_STATE:-.}/disk-${key}.stamp"
    if [[ -z "${latest}" ]]; then
        echo "No acct.${key}.* in ${USAGE_DIR}; skipping."; continue
    fi
    if [[ -f "${stamp}" && ! "${latest}" -nt "${stamp}" ]]; then
        echo "${resource}: ${latest##*/} already processed"; continue
    fi
    echo "# ${resource}: ${latest}"
    # --skip-errors: a partial load is better than none. sam-admin exits 2 only for an
    # unexpected gap (no project, no account, unknown user). The file is consumed either way,
    # so it is stamped on 0 or 2: reloading it would repeat the same rows. To rerun, rm its stamp.
    time sam-admin accounting --disk --resource "${resource}" --user-usage "${latest}" \
            --verbose --skip-errors "$@"; s=$?
    case ${s} in
        0|2) [[ " $* " == *" --dry-run "* ]] || date > "${stamp}"; (( s == 0 )) || rc=2 ;;
        *)   rc=2 ;;
    esac
done <<'MAP'
quasar Quasar
glade Campaign_Store
desc1 Destor
MAP
exit ${rc}
