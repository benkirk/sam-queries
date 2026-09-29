#!/bin/bash
# nhd_lane_summary.sh LANE: one record per line about an ncar-hpc-deploy lane, read from
# the files under lanes/LANE, never through the deploy CLI (whose `status` creates the lane
# directories). Ages are integer seconds computed here, so the reader parses no timestamps.
# scripts/cirrus_watch.sh sends it over ssh (`ssh HOST bash -s -- LANE < this`); NHD_LANES
# points it at a fixture tree in tests. Records:
#   lane=L installed=0|1 now=EPOCH
#   image current=NAME digest=sha256:... git_sha=SHA previous=NAME|- candidate=0|1
#   update age=N outcome=unchanged|blessed|failed|unknown [detail=...]
#   tick CADENCE HOST age=N exit=E dur=D steps=job=rc,...
#   run JOB HOST age=N exit=E dur=D [reason=...]        (reason only when exit != 0)
#   lock NAME age=N                                     (only past NHD_STALE_S, 3600)
#   spool HOST SYSTEM age=N
set -u
lane="${1:?usage: nhd_lane_summary.sh LANE}"
lanes="${NHD_LANES:-/glade/u/apps/opt/sam-queries/containers/ncar-hpc-deploy/lanes}"
R="${lanes}/${lane}"; S="${R}/state"; L="${R}/logs"
now=$(date +%s)
stale_s="${NHD_STALE_S:-3600}"

mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null; }
age()   { local m; m=$(mtime "$1") || return 1; echo $(( now - m )); }
name()  { local b; b=$(readlink "$1" 2>/dev/null) || return 1; b=${b##*/}; echo "${b%.sif}"; }

if [[ ! -d "${S}" ]]; then echo "lane=${lane} installed=0 now=${now}"; exit 0; fi
echo "lane=${lane} installed=1 now=${now}"

cur=$(name "${R}/current" || echo '-')
prev=$(name "${R}/previous" || echo '-')
cand=0; [[ -e "${R}/candidate" ]] && cand=1
digest=$(cat "${S}/last-digest" 2>/dev/null || echo '-')
# update-history rows: <ts> <digest> <sif> [git_sha=SHA]; the current image's row names its sha.
git_sha=$(grep -F " ${cur}.sif" "${S}/update-history" 2>/dev/null | tail -1 \
    | sed -n 's/.*git_sha=\([0-9a-f]*\).*/\1/p')
echo "image current=${cur} digest=${digest} git_sha=${git_sha:-?} previous=${prev} candidate=${cand}"

# state/last-update (<ts> <outcome> <digest> ...) is written by every update run; before it
# exists the update lock's mtime is the only trace and the outcome is unknown.
if [[ -f "${S}/last-update" ]]; then
    read -r _ outcome _ rest < "${S}/last-update" || true
    echo "update age=$(age "${S}/last-update") outcome=${outcome:-unknown}${rest:+ detail=${rest}}"
elif [[ -f "${S}/update.lock" ]]; then
    echo "update age=$(age "${S}/update.lock") outcome=unknown"
fi
[[ -f "${S}/last-update.FAILED" ]] && echo "update age=$(age "${S}/last-update.FAILED") outcome=failed detail=$(tr -s ' \n' ' ' < "${S}/last-update.FAILED")"

for f in "${S}"/last-tick.*.*; do
    [[ -f "${f}" ]] || continue
    n=${f##*/last-tick.}; cadence=${n%%.*}; host=${n#*.}
    read -r _ ex dur steps < "${f}" || true
    echo "tick ${cadence} ${host} age=$(age "${f}") ${ex:-exit=?} dur=${dur%s} steps=${steps:--}"
done

# The reason is the last plain line of the run's block in logs/<job>/<host>-DATE.log: not a
# [timestamp] line, not the separator, so it is the job's own final word ("All fallbacks failed").
reason_for() {
    local log; log=$(ls -t "${L}/$1/$2-"*.log 2>/dev/null | head -1) || return 0
    [[ -n "${log}" ]] || return 0
    awk '
        /^#-+$/ { last = ""; next }
        /^\[[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T/ { if ($0 ~ / exit=/) reason = last; next }
        NF { last = substr($0, 1, 80) }
        END { print reason }' "${log}"
}
for f in "${S}"/last-run.*.*; do
    [[ -f "${f}" ]] || continue
    n=${f##*/last-run.}; job=${n%%.*}; host=${n#*.}
    read -r _ ex dur < "${f}" || true
    line="run ${job} ${host} age=$(age "${f}") ${ex:-exit=?} dur=${dur%s}"
    [[ "${ex:-}" == "exit=0" ]] || line+=" reason=$(reason_for "${job}" "${host}")"
    echo "${line}"
done

for f in "${S}"/*.lock; do
    [[ -f "${f}" ]] || continue
    a=$(age "${f}") || continue
    (( a > stale_s )) && echo "lock $(basename "${f}" .lock) age=${a}"
done

for d in "${S}"/spool/*/; do
    [[ -d "${d}" ]] || continue
    host=$(basename "${d}")
    for s in "${d}"*; do
        [[ -L "${s}" && -f "${s}/scrape.meta" ]] || continue
        echo "spool ${host} $(basename "${s}") age=$(age "${s}/scrape.meta")"
    done
done
exit 0
