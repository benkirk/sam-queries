#!/usr/bin/env bash
# Cache refresh: the one write in rest/. It clears SAM's server-side caches, all
# at once or one category at a time. Safe (caches are derived data), but it is
# not read-only: pages run slower until the caches warm again. Needs a key that
# holds SYSTEM_ADMIN in production.
source "$(dirname "$0")/../lib/connect.sh"

example "Clear every cache; the reply names each category and how many entries went"
CATEGORIES=$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
       "$SAM_API_BASE/api/v1/admin/cache/refresh" \
    | tee /dev/stderr | jq -er '.cleared | keys[]')

# The categories come from the reply above, so this never drifts from the server.
for category in $CATEGORIES; do
  example "Clear only the '$category' cache"
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
       "$SAM_API_BASE/api/v1/admin/cache/refresh?category=$category" \
    | jq -ec '.cleared'
done

example "An unknown category is a 400 that lists the valid ones"
code=$(curl -sS -o /dev/stderr -w '%{http_code}' -u "$SAM_API_USER:$SAM_API_PASS" -X POST \
            "$SAM_API_BASE/api/v1/admin/cache/refresh?category=no-such-cache")
[[ $code == 400 ]] || { echo "expected 400, got $code" >&2; exit 1; }
