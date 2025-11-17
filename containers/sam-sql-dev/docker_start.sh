#!/usr/bin/env bash
set -e

CONTAINER_NAME="local-sam-mysql"
MYSQL_ROOT_PASSWORD="root"
MYSQL_IMAGE="mysql:9"
waittime=20

# Parse optional backup file argument
BACKUP_FILE=""
RESTORE_MODE=false

if [ $# -gt 0 ]; then
  BACKUP_FILE="$1"

  # Validate backup file exists
  if [ ! -f "${BACKUP_FILE}" ]; then
    echo "❌ Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
  fi

  # Get absolute path
  BACKUP_FILE="$(cd "$(dirname "${BACKUP_FILE}")" && pwd)/$(basename "${BACKUP_FILE}")"
  RESTORE_MODE=true

  echo "📦 Restore mode enabled: ${BACKUP_FILE}"
  echo "⚠️  Volume will be removed to ensure clean restore"
fi

echo "🧱 Starting local MySQL Docker container \"${CONTAINER_NAME}\"..."
docker rm -f ${CONTAINER_NAME} >/dev/null 2>&1 || true

# Remove volume if restoring from backup (required for /docker-entrypoint-initdb.d to run)
if [ "${RESTORE_MODE}" = true ]; then
  echo "🗑️  Removing existing volume for clean restore..."
  docker volume rm ${CONTAINER_NAME}-vol >/dev/null 2>&1 || true
  waittime=45  # Increase wait time for restore
fi

# Build docker run command with optional backup mount
DOCKER_CMD="docker run -d --name ${CONTAINER_NAME} \
  -e MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD} \
  -e MYSQL_DATABASE=sam \
  -p 3306:3306 \
  -v ${CONTAINER_NAME}-vol:/var/lib/mysql"

if [ "${RESTORE_MODE}" = true ]; then
  DOCKER_CMD="${DOCKER_CMD} -v ${BACKUP_FILE}:/docker-entrypoint-initdb.d/backup.sql.xz:ro"
fi

DOCKER_CMD="${DOCKER_CMD} ${MYSQL_IMAGE}"

# Execute docker run
eval ${DOCKER_CMD}

if [ "${RESTORE_MODE}" = true ]; then
  echo "⏳ Waiting ${waittime} seconds for \"${CONTAINER_NAME}\" to initialize and restore backup..."
  echo "   (This may take longer depending on backup size)"
else
  echo "⏳ Waiting ${waittime} seconds for \"${CONTAINER_NAME}\" to initialize..."
fi

sleep ${waittime}

echo "✅ Local MySQL \"${CONTAINER_NAME}\" container ready."

if [ "${RESTORE_MODE}" = true ]; then
  echo "✅ Backup restored from: ${BACKUP_FILE}"
  # (mysqlcheck not in oracle/mysql image...
  #docker exec ${CONTAINER_NAME} sh -c 'mysqlcheck -a --all-databases -u root -proot'
fi
