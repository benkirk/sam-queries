#!/bin/bash
# Symlink target for bin/<tool>: runs <tool> inside the lane's current image.
# Lane from NCAR_HPC_DEPLOY_LANE (default prod).
# Body in braces: bash parses it whole before running, so a `git pull` of this
# checkout mid-run cannot splice new bytes into a running update.
{
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lane.sh"
nhd_lane_init
NHD_JOB="$(basename "$0")"   # picks up env.<tool>, e.g. env.jobhist-sync
nhd_exec "${NHD_LANE_DIR}/current" "$(basename "$0")" "$@"
exit
}
