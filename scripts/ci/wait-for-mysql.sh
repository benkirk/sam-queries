#!/usr/bin/env bash
# wait-for-mysql.sh — Poll MySQL over TCP until it accepts connections
# and (optionally) verify a database is queryable.
#
# Used by CI workflows to wait for Docker MySQL containers to finish
# initialization. During init, MySQL runs with --skip-networking (socket
# only); TCP becomes available only after init scripts complete and
# MySQL restarts. Do NOT use `docker compose exec` — it uses Unix socket
# inside the container and gives false positives during init.
#
# Configuration via environment variables (all have sane defaults):
#
#   MYSQL_HOST         Host to connect to           (default: 127.0.0.1)
#   MYSQL_PORT         TCP port                     (default: 3306)
#   MYSQL_USER         Username                     (default: root)
#   MYSQL_PASS         Password                     (default: root)
#   MYSQL_DB           Database to verify            (default: sam; empty = skip)
#   COMPOSE_SERVICE    Compose service for state check (default: empty = skip)
#   TCP_RETRIES        Max TCP poll attempts         (default: 60)
#   DB_RETRIES         Max DB verification attempts  (default: 12)
#   RETRY_INTERVAL     Seconds between retries       (default: 5)
#   LOG_INTERVAL       Log progress every N retries  (default: 6)
#
# Usage in GitHub Actions:
#
#   - name: Wait for MySQL (main)
#     run: scripts/ci/wait-for-mysql.sh
#     env:
#       COMPOSE_SERVICE: mysql
#
#   - name: Wait for MySQL (test, port 3307)
#     run: scripts/ci/wait-for-mysql.sh
#     env:
#       MYSQL_PORT: "3307"
#       COMPOSE_SERVICE: mysql-test

set -euo pipefail

MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASS="${MYSQL_PASS:-root}"
MYSQL_DB="${MYSQL_DB:-sam}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-}"
TCP_RETRIES="${TCP_RETRIES:-60}"
DB_RETRIES="${DB_RETRIES:-12}"
RETRY_INTERVAL="${RETRY_INTERVAL:-5}"
LOG_INTERVAL="${LOG_INTERVAL:-6}"

label="${COMPOSE_SERVICE:-${MYSQL_HOST}:${MYSQL_PORT}}"

# ── Phase 1: TCP connectivity ────────────────────────────────────────

echo "=== Waiting for MySQL TCP on ${MYSQL_HOST}:${MYSQL_PORT} ==="

tcp_ok=false
for i in $(seq 1 "${TCP_RETRIES}"); do
    if mysql -h "${MYSQL_HOST}" -P "${MYSQL_PORT}" \
             -u"${MYSQL_USER}" -p"${MYSQL_PASS}" \
             -e "SELECT 1" 2>/dev/null; then
        echo "✅ ${label}: TCP connected after $((i * RETRY_INTERVAL))s"
        tcp_ok=true
        break
    fi

    # Optional: check if the compose service has exited (early abort)
    if [ -n "${COMPOSE_SERVICE}" ]; then
        state=$(docker compose ps "${COMPOSE_SERVICE}" --format "{{.State}}" 2>/dev/null || echo "unknown")
        if [ "${state}" = "exited" ]; then
            echo "❌ ${label}: container exited!"
            docker compose logs "${COMPOSE_SERVICE}" --tail=200
            exit 1
        fi
    fi

    if [ $((i % LOG_INTERVAL)) -eq 0 ]; then
        elapsed=$((i * RETRY_INTERVAL))
        if [ -n "${COMPOSE_SERVICE}" ]; then
            echo "  Still waiting... (${elapsed}s elapsed, container state: ${state:-unknown})"
        else
            echo "  Still waiting... (${elapsed}s elapsed)"
        fi
    fi

    sleep "${RETRY_INTERVAL}"
done

if [ "${tcp_ok}" != "true" ]; then
    echo "❌ ${label}: TCP connection timed out after $((TCP_RETRIES * RETRY_INTERVAL))s"
    [ -n "${COMPOSE_SERVICE}" ] && docker compose logs "${COMPOSE_SERVICE}" --tail=200
    exit 1
fi

# ── Phase 2: Database verification ───────────────────────────────────

if [ -z "${MYSQL_DB}" ]; then
    echo "  (MYSQL_DB not set — skipping database verification)"
    exit 0
fi

echo "=== Verifying ${MYSQL_DB} database on ${label} ==="

db_ok=false
for i in $(seq 1 "${DB_RETRIES}"); do
    if mysql -h "${MYSQL_HOST}" -P "${MYSQL_PORT}" \
             -u"${MYSQL_USER}" -p"${MYSQL_PASS}" \
             "${MYSQL_DB}" -e "SELECT 1" 2>/dev/null; then
        echo "✅ ${label}: ${MYSQL_DB} database is ready"
        db_ok=true
        break
    fi
    echo "  Waiting for ${MYSQL_DB} database... ($((i * RETRY_INTERVAL))s)"
    sleep "${RETRY_INTERVAL}"
done

if [ "${db_ok}" != "true" ]; then
    echo "❌ ${label}: ${MYSQL_DB} database not accessible after $((DB_RETRIES * RETRY_INTERVAL))s"
    [ -n "${COMPOSE_SERVICE}" ] && docker compose logs "${COMPOSE_SERVICE}" --tail=100
    exit 1
fi
