#!/bin/bash
# Candidate-image gate, run inside the image with the lane env; one STEP per container
# so each job's env overlay applies. Read-only: every step is --dry-run or a query.
# Usage: smoke.sh base|accounting-comp|jobhist-sync|collectors
TIMEFORMAT='(%3R s)'
SRC=/code/containers/ncar-hpc-deploy
out="${NHD_STATE:-/tmp}/smoke-${NCAR_HOST}-$1.out"
run() { echo "== smoke $1: ${*:2}"; time "${@:2}" > "${out}" 2>&1 \
        || { tail -40 "${out}" >&2; echo "SMOKE FAIL: $1" >&2; exit 2; }; }

case "$1" in
    base)
        echo "== image git_sha=${GIT_SHA:-?} built=${BUILD_DATE:-?} host=${NCAR_HOST} lane=${NHD_LANE}"
        # An image built before this directory merged has no jobs; name that plainly.
        run jobs-shipped ls "${SRC}/jobs"
        # A bad etc/schedule would make every tick die; refuse the image instead.
        run schedule awk -v src="${SRC}" '/^[[:space:]]*(#|$)/ { next }
            NF < 4 || $1 !~ /^[a-z0-9][a-z0-9-]*$/ || $4 !~ /^[a-z0-9][a-z0-9-]*$/ \
                || $2 !~ /^(casper|derecho)(,(casper|derecho))*$/ || $3 !~ /^(prod|dev)(,(prod|dev))*$/ \
                || system("test -f " src "/jobs/" $4 ".sh") { print "bad row " NR ": " $0; bad = 1 }
            END { exit bad }' "${SRC}/etc/schedule"
        run imports python3 -c 'import sam, cli, job_history, webapp'
        run entry-points bash -c 'sam-admin --help && sam-search --help && jobhist-sync --help'
        run sam-db sam-search --format json project SCSG0001 ;;
    accounting-comp) run "$1" bash "${SRC}/jobs/accounting-comp.sh" --last 1d --dry-run ;;
    # --dry-run runs the read-only schema check; a schema that is behind fails the candidate.
    jobhist-sync)    run "$1" bash "${SRC}/jobs/jobhist-sync.sh" rapid --dry-run ;;
    collectors)      run "$1" bash "${SRC}/jobs/collectors.sh" --strict --dry-run ;;
    *) echo "unknown smoke step '$1'" >&2; exit 2 ;;
esac
