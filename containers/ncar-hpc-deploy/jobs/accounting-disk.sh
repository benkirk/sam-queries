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
    # --skip-errors: a partial load is better than none; errors still print.
    if time sam-admin accounting --disk --resource "${resource}" --user-usage "${latest}" \
            --verbose --skip-errors "$@"; then
        [[ " $* " == *" --dry-run "* ]] || date > "${stamp}"
    else
        rc=2
    fi
done <<'MAP'
quasar Quasar
glade Campaign_Store
desc1 Destor
MAP
exit ${rc}
