#!/usr/bin/env bash
# wait-for-postgres.sh — Poll the postgres-test compose service until
# pg_isready answers, then verify the target database accepts a query.
#
# Unlike MySQL, Postgres never listens before it is ready, so an in-container
# probe is trustworthy; no host client is required.
#
#   COMPOSE_SERVICE   compose service (default: postgres-test)
#   PG_USER / PG_DB   credentials to probe with (default: sam_test / sam)
#   RETRIES           attempts (default: 60); RETRY_INTERVAL seconds (default: 5)

set -euo pipefail

COMPOSE_SERVICE="${COMPOSE_SERVICE:-postgres-test}"
PG_USER="${PG_USER:-sam_test}"
PG_DB="${PG_DB:-sam}"
RETRIES="${RETRIES:-60}"
RETRY_INTERVAL="${RETRY_INTERVAL:-5}"

echo "=== Waiting for ${COMPOSE_SERVICE} (${PG_DB}) ==="
for i in $(seq 1 "${RETRIES}"); do
    if docker compose --profile test exec -T "${COMPOSE_SERVICE}" \
            pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1 \
       && docker compose --profile test exec -T "${COMPOSE_SERVICE}" \
            psql -U "${PG_USER}" -d "${PG_DB}" -Atc 'SELECT 1' >/dev/null 2>&1; then
        echo "✅ ${COMPOSE_SERVICE}: ready after $((i * RETRY_INTERVAL))s"
        exit 0
    fi
    state=$(docker compose --profile test ps "${COMPOSE_SERVICE}" --format "{{.State}}" 2>/dev/null || echo "unknown")
    if [ "${state}" = "exited" ]; then
        echo "❌ ${COMPOSE_SERVICE}: container exited!"
        docker compose --profile test logs "${COMPOSE_SERVICE}" --tail=200
        exit 1
    fi
    sleep "${RETRY_INTERVAL}"
done

echo "❌ ${COMPOSE_SERVICE}: not ready after $((RETRIES * RETRY_INTERVAL))s"
docker compose --profile test logs "${COMPOSE_SERVICE}" --tail=200
exit 1
