#!/usr/bin/env bash
# FairShare resource_group: the PBS fair-share tree for one machine, built from the
# fstree data with PBS's own capacities. Arguments:
#   rollup   none (default) | group | facility | all: roll shares up from actual
#            allocations, to that tier
#   equalize none | leaf | facility, comma-combinable: weigh peers equally, ignoring
#            allocation size ('facility' conflicts with rollup=facility or all)
#   format   json (default; lines + the capacities used) | text (the resource_group file)
# Results are cached per argument set; clear_cache=1 recomputes.
source "$(dirname "$0")/../lib/connect.sh"

# The tree needs the hpc-scheduling-tools plugin, which not every deployment loads.
# A server without it answers 503: say so and stop, rather than fail.
code=$(curl -sS -o /dev/null -w '%{http_code}' -u "$SAM_API_USER:$SAM_API_PASS" \
            "$SAM_API_BASE/api/v1/fairshare/Derecho")
if [[ $code == 503 ]]; then echo "(this server has no fairshare plugin; skipping)"; exit 0; fi

example "Derecho, defaults: top-down shares from the allocations"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fairshare/Derecho" \
  | jq -e '{machine, rollup, equalize, capacities, lines: (.lines | length),
            first_lines: .lines[:5], warnings: (.warnings | length)}'

example "Derecho, rollup=facility&equalize=leaf: every project account weighs the same"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fairshare/Derecho?rollup=facility&equalize=leaf" \
  | jq -e '{rollup, equalize, first_lines: .lines[:5], warnings: .warnings[:3]}'

example "Casper, equalize=facility: facilities split evenly, as the resource_group file"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fairshare/Casper?equalize=facility&format=text" \
  | sed -n '1,10p'

example "Casper, rollup=group: shares rolled up to the project-group tier"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fairshare/Casper?rollup=group" \
  | jq -e '{rollup, lines: (.lines | length), first_lines: .lines[:5]}'

example "A conflicting combination is a 400 that says why"
code=$(curl -sS -o /dev/stderr -w '%{http_code}' -u "$SAM_API_USER:$SAM_API_PASS" \
            "$SAM_API_BASE/api/v1/fairshare/Derecho?rollup=facility&equalize=facility")
[[ $code == 400 ]] || { echo "expected 400, got $code" >&2; exit 1; }
