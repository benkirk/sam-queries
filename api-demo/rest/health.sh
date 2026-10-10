#!/usr/bin/env bash
# Health: is SAM up, and can it reach its databases? No API key needed.
source "$(dirname "$0")/../lib/connect.sh"

example "Liveness: the web process answers"
curl -sS --fail-with-body "$SAM_API_BASE/api/v1/health/live" | jq -e '.status == "alive"'

example "Readiness: each database it depends on, with latency"
curl -sS --fail-with-body "$SAM_API_BASE/api/v1/health/ready" \
  | jq -e '{status, checks: (.checks | map_values({status, latency_ms}))}'
