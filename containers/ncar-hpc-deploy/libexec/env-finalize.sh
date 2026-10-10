#!/usr/bin/env bash
# Install a lane env bundle (HPC_LANE_ENV_LAYERING.md § 6a), as csgteam, from the extracted bundle.
#   ./env-finalize.sh              dry run: verify the bundle, print what would change
#   ./env-finalize.sh --apply      back up, install atomically, print the result
#   ./env-finalize.sh --rollback   undo the newest --apply (restore backups, remove new files)
# MANIFEST rows: "file PATH MODE GROUP SHA256" or "link PATH TARGET", PATH relative to the deploy root.
# Overrides, for tests only: NHD_FINALIZE_ROOT, NHD_FINALIZE_USER, NHD_FINALIZE_GROUP.
set -euo pipefail

mode=dry
case "${1:-}" in
    '') ;;
    --apply) mode=apply ;;
    --rollback) mode=rollback ;;
    *) echo "usage: $0 [--apply|--rollback]" >&2; exit 2 ;;
esac

bundle=$(cd "$(dirname "$0")" && pwd)
root="${NHD_FINALIZE_ROOT:-/glade/u/apps/opt/sam-queries/containers/ncar-hpc-deploy}"
want_user="${NHD_FINALIZE_USER:-csgteam}"
stamp=$(date +%Y%m%d-%H%M%S)
die() { echo "env-finalize: $*" >&2; exit 2; }
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

[[ "$(id -un)" == "${want_user}" ]] || die "run as ${want_user}, not $(id -un)"
[[ -d "${root}/lanes" ]] || die "no lanes/ under ${root}"
[[ -r "${bundle}/MANIFEST" ]] || die "no MANIFEST beside $0"
umask 077

if [[ "${mode}" == rollback ]]; then
    log=$(ls -1t "${root}"/lanes/.env-finalize-*.log 2>/dev/null | head -1) || true
    [[ -n "${log}" ]] || die "no install log under ${root}/lanes"
    echo "rolling back ${log}"
    while read -r kind path backup; do
        if [[ "${backup}" != - ]]; then mv -f "${root}/${backup}" "${root}/${path}"; echo "  restored ${path}"
        else rm -f "${root}/${path}"; echo "  removed  ${path}"; fi
    done < "${log}"
    mv "${log}" "${log}.rolled-back"
    exit 0
fi

# Verify every bundle file against the manifest before touching anything.
while read -r kind path a b c; do
    [[ "${kind}" == file ]] || continue
    [[ -f "${bundle}/${path}" ]] || die "bundle is missing ${path}"
    [[ "$(sha "${bundle}/${path}")" == "${c}" ]] || die "checksum mismatch: ${path}"
done < "${bundle}/MANIFEST"
echo "bundle verified: $(grep -c . "${bundle}/MANIFEST") entries"

log="${root}/lanes/.env-finalize-${stamp}.log"
while read -r kind path a b c; do
    target="${root}/${path}"
    backup=-
    if [[ -e "${target}" || -L "${target}" ]]; then
        if [[ "${kind}" == file && -f "${target}" && ! -L "${target}" && "$(sha "${target}")" == "${c}" ]]; then
            echo "  same     ${path}"; continue
        fi
        if [[ "${kind}" == link && -L "${target}" && "$(readlink "${target}")" == "${a}" ]]; then
            echo "  same     ${path} -> ${a}"; continue
        fi
        backup="${path}.bak-${stamp}"
    fi
    if [[ "${mode}" == dry ]]; then
        if [[ "${kind}" == file ]]; then
            echo "  install  ${path}  ${a} ${NHD_FINALIZE_GROUP:-${b}}$([[ ${backup} != - ]] && echo "  (backup ${backup})")"
        else
            echo "  link     ${path} -> ${a}$([[ ${backup} != - ]] && echo "  (backup ${backup})")"
        fi
        continue
    fi
    [[ "${backup}" != - ]] && cp -P -p "${target}" "${root}/${backup}"
    tmp="${target}.new-${stamp}"
    if [[ "${kind}" == file ]]; then
        cp "${bundle}/${path}" "${tmp}"
        chgrp "${NHD_FINALIZE_GROUP:-${b}}" "${tmp}"
        chmod "${a}" "${tmp}"
        mv -f "${tmp}" "${target}"
    else
        ln -s "${a}" "${tmp}"
        mv -f "${tmp}" "${target}"
    fi
    echo "${kind} ${path} ${backup}" >> "${log}"
    echo "  done     ${path}"
done < "${bundle}/MANIFEST"

if [[ "${mode}" == dry ]]; then
    echo "dry run: nothing written. Re-run with --apply."
    exit 0
fi
echo "installed; rollback with: $0 --rollback   (log ${log})"
for lane in $(cut -d' ' -f2 "${bundle}/MANIFEST" | cut -d/ -f2 | sort -u); do
    ls -l "${root}/lanes/${lane}"/env*
done
