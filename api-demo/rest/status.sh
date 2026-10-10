#!/usr/bin/env bash
# System status: the latest snapshot the collectors posted for each system, plus
# outages and reservations.
source "$(dirname "$0")/../lib/connect.sh"

# A "latest" route answers 404 until a collector has posted for that system, so
# these three read the HTTP code before handing the body to jq.
body=$(mktemp)
trap 'rm -f "$body"' EXIT
for system in derecho casper jupyterhub; do
  example "Latest $system snapshot (top-level fields)"
  code=$(curl -sS -o "$body" -w '%{http_code}' -u "$SAM_API_USER:$SAM_API_PASS" \
              "$SAM_API_BASE/api/v1/status/$system/latest")
  if [[ $code == 404 ]]; then echo "(no $system snapshot yet: no collector has posted)"; continue; fi
  [[ $code == 200 ]] || { cat "$body"; exit 1; }
  jq -e 'with_entries(select(.value | type | IN("array", "object") | not))' "$body"
done

example "Current and upcoming outages"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/status/outages" \
  | jq '{outages: length, titles: [.[].title]}'

example "Reservations"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/status/reservations" \
  | jq '{reservations: length}'
