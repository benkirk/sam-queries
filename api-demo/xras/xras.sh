#!/usr/bin/env bash
# XRAS: the endpoints NSF's XRAS allocation system calls in SAM. Needs a key
# carrying the legacy ROLE_XRAS role; set SAM_XRAS_USER / SAM_XRAS_PASS if that
# is not your default key. Read-only here: XRAS's POSTs create real allocations.
source "$(dirname "$0")/../lib/connect.sh"
KEY="${SAM_XRAS_USER:-$SAM_API_USER}:${SAM_XRAS_PASS:-$SAM_API_PASS}"
XRAS="$SAM_API_BASE/api/xras/v1"

USERNAME=${SAM_DEMO_USERNAME:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].lead_username')}

example "A person as XRAS sees them ($USERNAME): the field names"
curl -sS --fail-with-body -u "$KEY" "$XRAS/people/$USERNAME" | jq -e 'keys'

example "Their allocation requests, per request number"
curl -sS --fail-with-body -u "$KEY" "$XRAS/requests/user/$USERNAME" \
  | jq -e '[.result.masters[] | {requestNumber,
            requests: [.requests[] | {requestType, allocationType, projectId}]}]'
