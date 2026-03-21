#!/bin/bash
set -euo pipefail

# Manual deployment to staging (alternative to git push).
# Builds Docker image, pushes to ECR, and updates ECS service.
#
# Usage:
#   ./scripts/infra/deploy-staging.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/../lib/prereqs.sh"

# --- Prerequisites ---
check_aws_cli
check_docker
require_cmd terraform terraform terraform "Terraform"
require_cmd git git git "Git"

REGION="us-east-1"
CLUSTER="sam-staging"
SERVICE="sam-staging-webapp"
CONTAINER="sam-webapp"

cd "$REPO_ROOT"

# Get ECR repo URL from Terraform
cd infrastructure/staging
ECR_URL=$(terraform output -raw ecr_repository_url 2>/dev/null)
cd "$REPO_ROOT"

if [ -z "$ECR_URL" ]; then
    echo "ERROR: Could not read ecr_repository_url. Run 'terraform apply' first."
    exit 1
fi

ACCOUNT_ID=$(echo "$ECR_URL" | cut -d. -f1)
IMAGE_TAG=$(git rev-parse --short HEAD)

echo "=== Deploying to Staging ==="
echo "ECR: $ECR_URL"
echo "Tag: $IMAGE_TAG"
echo ""

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# Build
echo "Building Docker image..."
docker build -f containers/webapp/Dockerfile -t "$ECR_URL:$IMAGE_TAG" -t "$ECR_URL:latest" .

# Push
echo "Pushing to ECR..."
docker push "$ECR_URL:$IMAGE_TAG"
docker push "$ECR_URL:latest"

# Update ECS service
echo "Updating ECS service..."
aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "$SERVICE" \
    --force-new-deployment \
    --region "$REGION" \
    --query 'service.serviceName' \
    --output text

echo ""
echo "Deployment triggered. Monitor with:"
echo "  aws ecs describe-services --cluster $CLUSTER --services $SERVICE --query 'services[0].deployments' --region $REGION"
