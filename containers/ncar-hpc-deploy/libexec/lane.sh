#!/bin/bash
# Shared prologue for ncar-hpc-deploy host scripts: lane layout, apptainer, binds.
# Sourced, never executed. Layout and lifecycle: containers/ncar-hpc-deploy/README.md.

NHD_LIBEXEC="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
NHD_ROOT="${NCAR_HPC_DEPLOY_ROOT:-$(dirname "${NHD_LIBEXEC}")}"
NHD_LANE="${NCAR_HPC_DEPLOY_LANE:-prod}"
NHD_IMAGE_REPO="${NCAR_HPC_DEPLOY_IMAGE_REPO:-ghcr.io/benkirk/sam-queries/samuel}"
# Job bodies ship inside the image; NCAR_HPC_DEPLOY_SRC=<checkout> binds that checkout's
# jobs and collectors over the image's (testing a change before its image exists).
NHD_IMAGE_SRC=/code/containers/ncar-hpc-deploy
# Host-side python (lock.py, the GHCR token): the system one, not whatever module view the
# login shell puts first on PATH. Code under libexec/ must stay 3.6-compatible.
NHD_PYTHON="${NCAR_HPC_DEPLOY_PYTHON:-/usr/bin/python3}"

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
    # NHD_NO_CREATE: a read-only command (status) on a lane that may not exist, or as a watcher account.
    [[ -n "${NHD_NO_CREATE:-}" ]] && return 0
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

# Tools refused, rather than run on the public layer, when env.<tool> exists but is unreadable.
NHD_GATED_TOOLS="sam-admin jobhist-sync"

# apptainer exec in IMAGE with the lane's env layers; caller supplies the command.
# Layers: lanes/<lane>/env (readers, any user), then env.<job> when this user can read it.
nhd_exec() {
    local image="$1"; shift
    [[ -s "${image}" ]] || nhd_die "no image, or an empty one, at ${image} (run: ncar-hpc-deploy update --lane ${NHD_LANE})"
    [[ -r "${NHD_ENV_FILE}" ]] || nhd_die "missing lane env ${NHD_ENV_FILE}"
    local layers=("${NHD_ENV_FILE}") overlay="${NHD_ENV_FILE}.${NHD_JOB}"
    if [[ -n "${NHD_JOB}" && -r "${overlay}" ]]; then
        layers+=("${overlay}")
    elif [[ -n "${NHD_JOB}" && -e "${overlay}" && " ${NHD_GATED_TOOLS} " == *" ${NHD_JOB} "* ]]; then
        nhd_die "${NHD_JOB} needs read access to ${overlay} (group $(ls -lLd "${overlay}" | awk '{print $4}'))"
    fi
    nhd_load_apptainer

    # Merged privately (a plain user cannot write state/). Signals become exits so the EXIT
    # trap removes the secrets; never exec apptainer here, which would skip the traps.
    local env_file rc
    env_file=$(umask 077; mktemp "${TMPDIR:-/tmp}/nhd-env.XXXXXX") || nhd_die "mktemp failed"
    trap 'rm -f "'"${env_file}"'"' EXIT
    trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
    # awk 1, not cat: a layer without a final newline would fuse with the next one's first line.
    awk 1 "${layers[@]}" > "${env_file}" || nhd_die "cannot merge ${layers[*]}"
    [[ -n "${NHD_DEBUG}" ]] && echo "ncar-hpc-deploy: env layers: ${layers[*]#"${NHD_ROOT}/"}" >&2
    local mpl="${NHD_STATE}/mpl"
    [[ -w "${NHD_STATE}" ]] || mpl="${TMPDIR:-/tmp}/mpl-$(id -un)"

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
        --env "NCAR_HOST=$(nhd_host),TZ=${NCAR_HPC_DEPLOY_TZ:-America/Denver},NHD_LANE=${NHD_LANE},NHD_STATE=${NHD_STATE},NHD_LOGS=${NHD_LOGS},MPLCONFIGDIR=${mpl}" \
        --env "NHD_COLLECTORS=$(nhd_collectors),NHD_SPOOL=${NHD_STATE}/spool/$(nhd_host)" \
        --pwd "${NHD_PWD:-${PWD}}" \
        "${image}" "$@"
    rc=$?
    rm -f "${env_file}"
    return ${rc}
}
