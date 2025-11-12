#!/usr/bin/env bash
set -e

CONTAINER_NAME="local-sam-mysql"
MYSQL_ROOT_PASSWORD="root"
MYSQL_IMAGE="mysql:9"

echo "🧱 Starting local MySQL Docker container..."
docker rm -f $CONTAINER_NAME >/dev/null 2>&1 || true
docker run -d --name $CONTAINER_NAME \
  -e MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD \
  -e MYSQL_DATABASE=sam \
  -p 3306:3306 $MYSQL_IMAGE

echo "⏳ Waiting for MySQL to initialize..."
sleep 20

echo "✅ Local MySQL container ready."

# mysql -u root -h 127.0.0.1 -proot sam
