#!/usr/bin/env bash
set -e

CONTAINER_NAME="local-sam-mysql"
MYSQL_ROOT_PASSWORD="root"
MYSQL_IMAGE="mysql:9"
waittime=20

echo "🧱 Starting local MySQL Docker container \"${CONTAINER_NAME}\"..."
docker rm -f ${CONTAINER_NAME} >/dev/null 2>&1 || true
docker run -d --name ${CONTAINER_NAME} \
  -e MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD} \
  -e MYSQL_DATABASE=sam \
  -p 3306:3306 \
  -v ${CONTAINER_NAME}-vol:/var/lib/mysql \
  ${MYSQL_IMAGE}

echo "⏳ Waiting ${waittime} seconds for \"${CONTAINER_NAME}\" to initialize..."
sleep ${waittime}

echo "✅ Local MySQL \"${CONTAINER_NAME}\" container ready."
