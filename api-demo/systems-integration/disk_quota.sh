#!/usr/bin/env bash
# Disk quotas: each project group's storage quota and paths, per disk resource.
# Cached for 5 minutes; POST .../refresh clears it.
source "$(dirname "$0")/../lib/connect.sh"

example "How many quota entries each disk resource has"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/disk_quota/" \
  | jq -e 'group_by(.resourceName) | map({resource: .[0].resourceName, entries: length})'

example "The first three entries"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/disk_quota/" \
  | jq '[.[:3][] | {projcode, groupName, resourceName, quota, paths: (.paths | length)}]'

example "Refresh"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/disk_quota/refresh" | jq -e '.status == "ok"'
