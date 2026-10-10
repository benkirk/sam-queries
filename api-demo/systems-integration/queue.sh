#!/usr/bin/env bash
# Queues: the batch queues SAM knows for each HPC resource, with their dates and
# wallclock limits. Cached for 5 minutes; POST .../refresh clears it.
source "$(dirname "$0")/../lib/connect.sh"

example "Every resource and its queues"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/queue/" \
  | jq -e '[.resources[] | {resource: .resourceName, queues: [.queues[].queueName]}]'

example "One resource (Derecho), with each queue's wallclock limit"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/queue/Derecho" \
  | jq -e '[.resources[].queues[] | {queueName, wallClockHoursLimit, startDate, endDate}]'

example "Refresh"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
     "$SAM_API_BASE/api/v1/queue/refresh" | jq -e '.status == "ok"'
