#!/usr/bin/env bash
# Allocations: one allocation by id, with its usage. Ids come from a project's
# allocation list.
source "$(dirname "$0")/../lib/connect.sh"

PROJCODE=${SAM_DEMO_PROJCODE:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].projcode')}

ALLOCATION_ID=$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/$PROJCODE/allocations" \
    | jq -er '.allocations[0].allocation_id')

example "Allocation $ALLOCATION_ID (the first one on $PROJCODE)"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/allocations/$ALLOCATION_ID" \
  | jq -e '{allocation_id, resource: .resource.resource_name, amount, used, remaining,
            percent_used, charges_by_type, start_date, end_date, is_active}'
