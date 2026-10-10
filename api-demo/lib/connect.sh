# shellcheck shell=bash
# Shared setup for every bash example. Sourced, never run.
#
# It only checks the environment; every example spells out its own curl call,
# so any one of them can be copied into a terminal as is.

set -euo pipefail

# An optional sam.env beside the tree (see sam.env.example) keeps the secret out
# of shell history. Variables it sets replace any already exported.
_demo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -f $_demo_root/sam.env ]]; then
    # shellcheck source=/dev/null
    source "$_demo_root/sam.env"
fi

for _tool in curl jq; do
    command -v "$_tool" >/dev/null || { echo "This demo needs $_tool on PATH." >&2; exit 2; }
done

: "${SAM_API_BASE:?Set SAM_API_BASE, e.g. https://samuel.k8s.ucar.edu (see sam.env.example)}"
: "${SAM_API_USER:?Set SAM_API_USER to your API key name}"
: "${SAM_API_PASS:?Set SAM_API_PASS to your API key secret}"
SAM_API_BASE=${SAM_API_BASE%/}

# A heading before each call, so the output reads in the same order as the script.
example() {
    if [[ -t 1 ]]; then printf '\n\033[1m== %s\033[0m\n' "$*"; else printf '\n== %s\n' "$*"; fi
}
