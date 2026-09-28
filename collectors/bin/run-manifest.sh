#!/bin/bash
# Host half of spool mode: run a collector manifest locally and capture each command.
#   collector.py --print-manifest | run-manifest.sh SPOOL_DIR
# stdin: key<TAB>command lines. Writes <key>.out/.err/.rc plus scrape.meta, then
# repoints SPOOL_DIR (a symlink) at the finished capture, so a reader never sees a
# partial spool. Needs bash + coreutils only: the host never needs our Python env.
# CMD_TIMEOUT (default 60s) bounds each command; all commands run concurrently.
# The collector's PBS_COMMAND_TIMEOUT / SSH_TIMEOUT apply to ssh mode only.
# Run it from a login environment: commands resolve on the caller's PATH, as they do
# over `ssh <host> "cmd"`.
set -u
spool="${1:?usage: run-manifest.sh SPOOL_DIR < manifest.tsv}"
spool="${spool%/}"
started=$(date +%s)
capture="${spool}.${started}.$$"
mkdir -p "${capture}" || exit 2

n=0
while IFS=$'\t' read -r key cmd; do
    [[ -z "${key}" ]] && continue
    if [[ ! "${key}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ || -z "${cmd}" ]]; then
        echo "run-manifest: bad manifest line for key '${key}'" >&2
        rm -rf "${capture}"; exit 2
    fi
    (
        timeout "${CMD_TIMEOUT:-60}" bash -c "${cmd}" \
            > "${capture}/${key}.out" 2> "${capture}/${key}.err" < /dev/null
        echo $? > "${capture}/${key}.rc"
    ) &
    n=$((n + 1))
done
wait
(( n > 0 )) || { echo "run-manifest: empty manifest" >&2; rm -rf "${capture}"; exit 2; }

# qstat recorded because only the site wrapper on the login PATH lists every job.
printf 'host=%s\nstarted=%s\nfinished=%s\ncommands=%s\nqstat=%s\n' \
    "$(hostname -s)" "${started}" "$(date +%s)" "${n}" "$(command -v qstat)" > "${capture}/scrape.meta"

# A plain directory left at SPOOL_DIR (first run by hand) is replaced by the symlink.
[[ -d "${spool}" && ! -L "${spool}" ]] && rm -rf "${spool}"
previous=$(readlink "${spool}" 2>/dev/null)
ln -sfn "$(basename "${capture}")" "${spool}.lnk.$$" && mv -Tf "${spool}.lnk.$$" "${spool}"

# Keep the new capture and the one it replaced (a reader may still be parsing it).
# Only finished captures (scrape.meta written) are removed: a concurrent run's
# in-progress capture has none yet.
for old in "${spool}".[0-9]*.[0-9]*; do
    [[ -f "${old}/scrape.meta" && "${old}" != "${capture}" \
        && "$(basename "${old}")" != "${previous}" ]] && rm -rf "${old}"
done

failed=$(grep -lv '^0$' "${capture}"/*.rc 2>/dev/null | xargs -r -n1 basename | sed 's/\.rc$//' | xargs)
[[ -n "${failed}" ]] && echo "run-manifest: nonzero exit from: ${failed}" >&2
exit 0
