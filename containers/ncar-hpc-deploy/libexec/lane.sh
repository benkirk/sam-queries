#!/bin/bash
# Shared prologue for ncar-hpc-deploy host scripts: lane layout, apptainer, binds.
# Sourced, never executed. Layout and lifecycle: containers/ncar-hpc-deploy/README.md.

NHD_LIBEXEC="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
NHD_ROOT="${NCAR_HPC_DEPLOY_ROOT:-$(dirname "${NHD_LIBEXEC}")}"
NHD_LANE="${NCAR_HPC_DEPLOY_LANE:-prod}"
NHD_IMAGE_REPO="${NCAR_HPC_DEPLOY_IMAGE_REPO:-ghcr.io/benkirk/sam-queries/webapp}"
# Job bodies ship inside the image; NCAR_HPC_DEPLOY_SRC=<checkout> binds that checkout's
# jobs and collectors over the image's (testing a change before its image exists).
NHD_IMAGE_SRC=/code/containers/ncar-hpc-deploy

nhd_die() { echo "ncar-hpc-deploy: $*" >&2; exit 2; }

nhd_lane_init() {
    case "${NHD_LANE}" in
        prod) NHD_TAG="${NCAR_HPC_DEPLOY_TAG:-main}" ;;
        dev)  NHD_TAG="${NCAR_HPC_DEPLOY_TAG:-staging}" ;;
        *)    nhd_die "unknown lane '${NHD_LANE}' (prod|dev)" ;;
    esac
    NHD_LANE_DIR="${NHD_ROOT}/lanes/${NHD_LANE}"
    NHD_ENV_FILE="${NHD_LANE_DIR}/env"
    NHD_STATE="${NHD_LANE_DIR}/state"
    NHD_LOGS="${NHD_LANE_DIR}/logs"
    mkdir -p "${NHD_LANE_DIR}/images" "${NHD_STATE}" "${NHD_LOGS}" || nhd_die "cannot create ${NHD_LANE_DIR}"
}

nhd_host() {
    if [[ -n "${NCAR_HOST}" ]]; then echo "${NCAR_HOST}"; return; fi
    case "$(hostname -s)" in
        casper*|crhtc*|crlogin*) echo casper ;;
        derecho*|dec*)           echo derecho ;;
        *)                       hostname -s ;;
    esac
}

# Each host collects only itself: ssh and PBS stay on the host (collectors/README.md).
nhd_collectors() {
    case "$(nhd_host)" in
        casper)  echo "casper jupyterhub" ;;
        derecho) echo "derecho" ;;
    esac
}

nhd_load_apptainer() {
    type apptainer >/dev/null 2>&1 && return
    type module >/dev/null 2>&1 || . /etc/profile.d/z00_modules.sh
    module load apptainer >/dev/null 2>&1 || nhd_die "module load apptainer failed"
}

# apptainer exec in IMAGE with the lane env; caller supplies the command.
nhd_exec() {
    local image="$1"; shift
    [[ -e "${image}" ]] || nhd_die "no image at ${image} (run: ncar-hpc-deploy update --lane ${NHD_LANE})"
    [[ -r "${NHD_ENV_FILE}" ]] || nhd_die "missing lane env ${NHD_ENV_FILE}"
    nhd_load_apptainer

    # env.<job> overlays the lane env for one job, e.g. a DB writer role only jobhist-sync needs.
    local env_file="${NHD_ENV_FILE}"
    if [[ -n "${NHD_JOB}" && -r "${NHD_ENV_FILE}.${NHD_JOB}" ]]; then
        env_file=$(umask 077; mktemp "${NHD_STATE}/.env.${NHD_JOB}.XXXXXX") || nhd_die "mktemp failed"
        cat "${NHD_ENV_FILE}" "${NHD_ENV_FILE}.${NHD_JOB}" > "${env_file}"
        trap 'rm -f "'"${env_file}"'"' EXIT
    fi

    local binds=(-B /glade)
    local d
    # PBS accounting logs (jobhist-sync) exist on one host each; bind whichever is here.
    for d in /ssg/pbs/casper/accounting /ncar/pbs/accounting /local_scratch; do
        [[ -d "${d}" ]] && binds+=(-B "${d}")
    done
    if [[ -n "${NCAR_HPC_DEPLOY_SRC}" ]]; then
        local src; src=$(readlink -f "${NCAR_HPC_DEPLOY_SRC}")
        [[ -d "${src}/containers/ncar-hpc-deploy" ]] || nhd_die "NCAR_HPC_DEPLOY_SRC must be a repo checkout"
        binds+=(-B "${src}/containers/ncar-hpc-deploy:${NHD_IMAGE_SRC}:ro" -B "${src}/collectors:/code/collectors:ro")
    fi

    # --cleanenv: nothing from the cron/login shell leaks in except what is named here.
    # TZ is explicit because the image's /etc/localtime is UTC; SAM dates are naive Mountain.
    apptainer --quiet exec --cleanenv \
        "${binds[@]}" \
        --env-file "${env_file}" \
        --env "NCAR_HOST=$(nhd_host),TZ=${NCAR_HPC_DEPLOY_TZ:-America/Denver},NHD_LANE=${NHD_LANE},NHD_STATE=${NHD_STATE},NHD_LOGS=${NHD_LOGS},MPLCONFIGDIR=${NHD_STATE}/mpl" \
        --env "NHD_COLLECTORS=$(nhd_collectors),NHD_SPOOL=${NHD_STATE}/spool/$(nhd_host)" \
        --pwd "${NHD_PWD:-${PWD}}" \
        "${image}" "$@"
}
