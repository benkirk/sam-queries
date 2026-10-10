#!/usr/bin/env bash
# Charges: what a project has used, rolled up per resource, and the detailed
# daily charge records behind it.
source "$(dirname "$0")/../lib/connect.sh"

PROJCODE=${SAM_DEMO_PROJCODE:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].projcode')}

example "$PROJCODE: allocated vs used per resource, for the current allocation"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/$PROJCODE/charges/summary" \
  | jq -e '.resources | map_values({allocated, used, percent_used, charges_by_type})'

example "$PROJCODE: daily charge records over the last 30 days, counted by type"
START=$(date -d '30 days ago' +%F 2>/dev/null || date -v-30d +%F)   # GNU or BSD date
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/$PROJCODE/charges?start_date=$START" \
  | jq -e '{start_date, end_date, records: (.charges | map_values(length))}'
