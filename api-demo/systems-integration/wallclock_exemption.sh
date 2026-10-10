#!/usr/bin/env bash
# Wallclock exemptions: users allowed to run past a queue's wallclock limit, per
# resource and queue. Cached for 5 minutes; POST .../refresh clears it.
source "$(dirname "$0")/../lib/connect.sh"

example "Exemptions per resource and queue"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/wallclock_exemption/" \
  | jq -e '[.resources[] | {resource: .resourceName,
           queues: [.queues[] | {queue: .queueName, exemptions: (.limits | length)}]}]'

example "One resource (Derecho): who is exempt, and to how many hours"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/wallclock_exemption/Derecho" \
  | jq '[.resources[].queues[] | {queue: .queueName, limits}]'

example "Refresh"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/wallclock_exemption/refresh" | jq -e '.status == "ok"'
