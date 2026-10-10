#!/usr/bin/env bash
# Directory Access: the unix accounts and groups SAM provisions on each access
# branch (hpc, hpc-data, hpc-dev). LDAP and provisioning tooling read this.
# Responses are cached for 5 minutes; POST .../refresh clears that cache.
source "$(dirname "$0")/../lib/connect.sh"

example "Every access branch, with how many accounts and groups it carries"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/directory_access/" \
  | jq -e '[.accessBranchDirectories[] | {branch: .accessBranchName,
           accounts: (.unixAccounts | length), groups: (.unixGroups | length)}]'

example "One branch (hpc): the first three accounts"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/directory_access/hpc" \
  | jq -e '[.accessBranchDirectories[].unixAccounts[:3][] | {username, uid, gid, homeDirectory}]'

example "Refresh: drop the cached response so the next read is rebuilt"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/directory_access/refresh" | jq -e '.status == "ok"'
