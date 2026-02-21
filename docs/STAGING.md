# Staging Environment

Guide for using the SAM Queries staging environment on AWS.

## Access

**URL**: `http://sam-staging-alb-1399416977.us-east-1.elb.amazonaws.com`

**Requirement**: UCAR VPN connection (128.117.0.0/16)

The staging site is only accessible from the UCAR network. Connect to VPN before accessing.

## What's Running

- **Flask webapp** on ECS Fargate (same Docker image as local dev)
- **MySQL 8.0** on RDS with obfuscated SAM data
- **ALB** handling HTTP traffic on port 80

Auth is disabled in staging (`DISABLE_AUTH=1`, auto-login as `benkirk`).

## Deployment

### Automatic (Recommended)

Push to the `staging` branch triggers GitHub Actions deployment:

```bash
git checkout staging
git merge main
git push origin staging
```

The workflow:
1. Builds Docker image from `containers/webapp/Dockerfile`
2. Pushes to ECR
3. Updates ECS task definition
4. Deploys new task and waits for stability

### Manual

```bash
./scripts/infra/deploy-staging.sh
```

## Accessing the Server

### SSH into Container

```bash
./scripts/infra/ssh-staging.sh
```

This uses ECS Exec to open an interactive shell in the running container. Requires:
- AWS CLI configured
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

### Query the Database

```bash
# Interactive MySQL session (requires VPN)
./scripts/infra/query-staging-db.sh

# Run a single query
./scripts/infra/query-staging-db.sh "SELECT COUNT(*) FROM users"
```

## Infrastructure Management

Infrastructure is managed with Terraform in `infrastructure/staging/`.

```bash
cd infrastructure/staging

# View outputs (URLs, endpoints)
terraform output

# Plan changes
terraform plan

# Apply changes
terraform apply
```

See [infrastructure/README.md](../infrastructure/README.md) for full details.

## Database

The staging database is a copy of the obfuscated SAM backup (`sam-obfuscated.sql.xz`). It contains anonymized data suitable for testing.

### Restoring the Database

If the database needs to be re-initialized:

```bash
cd infrastructure/staging
../scripts/init-rds.sh
```

### Connection Details

- **Host**: `sam-staging-mysql.c0ntwnweue47.us-east-1.rds.amazonaws.com`
- **Port**: 3306
- **Database**: sam
- **Credentials**: In `infrastructure/staging/secrets.auto.tfvars`

## GitHub Secrets

The deployment workflow requires these GitHub repository secrets:

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | Terraform IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | Terraform IAM user secret key |

Set these at: **Repository Settings > Secrets and variables > Actions**

## Troubleshooting

### Can't Access Staging URL

- Verify you're on UCAR VPN
- Check ALB is running: `terraform output staging_url`

### ECS Service Not Starting

```bash
# Check service events
aws ecs describe-services \
  --cluster sam-staging \
  --services sam-staging-webapp \
  --query 'services[0].events[:5]' \
  --region us-east-1

# Check task logs
aws logs tail /ecs/sam-staging-webapp --since 30m --region us-east-1
```

### Database Connection Issues

- Verify RDS is running: `terraform output rds_endpoint`
- Check security group allows your IP (UCAR CIDR)
- Test with: `./scripts/infra/query-staging-db.sh "SELECT 1"`

## Architecture

```
Internet → ALB (UCAR VPN only, port 80) → ECS Fargate (port 5050) → RDS MySQL (port 3306)
                                                    ↑
                                              ECR (Docker images)
                                              SSM (secrets)
```
