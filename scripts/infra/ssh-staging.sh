#!/bin/bash
set -euo pipefail

# Open an interactive shell in the staging ECS container.
# Uses ECS Exec (requires Session Manager plugin).
#
# Usage:
#   ./scripts/infra/ssh-staging.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/prereqs.sh"

# --- Prerequisites ---
check_aws_cli

if ! command -v session-manager-plugin &>/dev/null; then
    echo "ERROR: AWS Session Manager plugin is not installed."
    echo ""
    echo "  macOS:  brew install --cask session-manager-plugin"
    echo "  Linux:  https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html"
    exit 2
fi

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
