#!/usr/bin/env bash
# FairShare Tree: facilities -> allocation types -> projects -> resources, with
# allocation, usage and balance. PBS reads it to set fair-share. Also keyed by
# project and by user. Cached for 5 minutes; POST .../refresh clears it.
source "$(dirname "$0")/../lib/connect.sh"

example "The tree for one resource (Derecho), counted per facility"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fstree_access/Derecho" \
  | jq -e '[.facilities[] | {facility: .name, fairSharePercentage,
           projects: ([.allocationTypes[].projects[]] | length)}]'

# A resource name with a space must be URL-encoded: Derecho%20GPU.

PROJCODE=${SAM_DEMO_PROJCODE:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].projcode')}

example "One project ($PROJCODE): its resources, balance and users"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fstree_access/projects/$PROJCODE" \
  | jq -e '.[] | [.resources[] | {name, allocationAmount, adjustedUsage, balance,
                                          users: (.users | length)}]'

USERNAME=${SAM_DEMO_USERNAME:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/$PROJCODE" | jq -r '.lead.username')}

example "One user ($USERNAME): the projects and resources they can charge"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/fstree_access/users/$USERNAME" \
  | jq -e 'map_values(.projects | map_values([.resources[].name]))'

example "Refresh"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/fstree_access/refresh" | jq -e '.status == "ok"'
