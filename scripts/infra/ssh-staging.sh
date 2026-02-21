#!/bin/bash
set -euo pipefail

# Open an interactive shell in the staging ECS container.
# Uses ECS Exec (requires Session Manager plugin).
#
# Prerequisites:
#   - AWS CLI configured
#   - Session Manager plugin installed:
#     https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html
#
# Usage:
#   ./scripts/infra/ssh-staging.sh

CLUSTER="sam-staging"
SERVICE="sam-staging-webapp"
CONTAINER="sam-webapp"
REGION="us-east-1"

echo "Finding running task in $CLUSTER..."
TASK_ARN=$(aws ecs list-tasks \
    --cluster "$CLUSTER" \
    --service-name "$SERVICE" \
    --desired-status RUNNING \
    --region "$REGION" \
    --query 'taskArns[0]' \
    --output text)

if [ "$TASK_ARN" = "None" ] || [ -z "$TASK_ARN" ]; then
    echo "ERROR: No running tasks found in $SERVICE"
    exit 1
fi

TASK_ID=$(echo "$TASK_ARN" | awk -F'/' '{print $NF}')
echo "Connecting to task: $TASK_ID"
echo "Container: $CONTAINER"
echo ""

aws ecs execute-command \
    --cluster "$CLUSTER" \
    --task "$TASK_ID" \
    --container "$CONTAINER" \
    --region "$REGION" \
    --interactive \
    --command "/bin/bash"
