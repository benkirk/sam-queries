#!/usr/bin/env bash
# Project Access: per access branch, each project's unix group, its status and
# when its access expires. Cached for 5 minutes; POST .../refresh clears it.
source "$(dirname "$0")/../lib/connect.sh"

example "Project groups per branch"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/project_access/" \
  | jq -e 'map_values(length)'

example "One branch (hpc): the first three project groups"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/project_access/hpc" \
  | jq -e '[.hpc[:3][] | {groupName, status, expiration, resources: [.resourceGroupStatuses[].resourceName]}]'

example "Refresh"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/project_access/refresh" | jq -e '.status == "ok"'
