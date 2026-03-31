# SAM Queries Infrastructure

AWS infrastructure for the SAM Queries staging environment, managed with Terraform.

## Architecture

- **ECS Fargate** -- Flask webapp container (port 5050)
- **RDS MySQL 8.0** -- Database (restored from obfuscated backup)
- **ALB** -- Load balancer, restricted to UCAR VPN (128.117.0.0/16)
- **ECR** -- Docker image repository
- **SSM Parameter Store** -- Secrets (DB credentials, Flask key)

## AWS Account

- **Account**: 842264312439
- **Region**: us-east-1
- **IAM User**: terraform

## Directory Structure

```
infrastructure/
├── README.md              # This file
├── .gitignore             # Ignores .terraform, state, secrets
├── staging/
│   ├── main.tf            # Provider, backend config
│   ├── variables.tf       # Input variables
│   ├── terraform.tfvars   # Non-secret values
│   ├── secrets.auto.tfvars # Secret values (gitignored)
│   ├── outputs.tf         # Terraform outputs
│   ├── network.tf         # VPC, subnets, IGW, NAT
│   ├── security_groups.tf # ALB, ECS, RDS security groups
│   ├── alb.tf             # Application Load Balancer
│   ├── ecr.tf             # ECR repository
│   ├── ecs.tf             # ECS cluster, task def, service
│   ├── rds.tf             # RDS MySQL instance
│   ├── ssm.tf             # SSM Parameter Store
│   └── iam.tf             # IAM roles for ECS
└── scripts/
    └── init-rds.sh        # One-time DB restore script
```

## Quick Start

### Prerequisites

- Terraform >= 1.5
- AWS CLI configured with account 842264312439
- UCAR VPN connection (for RDS access)

### First-Time Setup

```bash
# 1. Navigate to staging
cd infrastructure/staging

# 2. Create secrets file (gitignored)
cat > secrets.auto.tfvars << 'EOF'
db_username      = "samadmin"
db_password      = "YourSecurePassword"
flask_secret_key = "your-flask-secret-key"
EOF

# 3. Initialize and apply
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=us-east-1

terraform init
terraform plan
terraform apply

# 4. Restore database (one-time, requires VPN)
../scripts/init-rds.sh
```

### Day-to-Day Operations

```bash
# View current state
terraform output

# Plan changes
terraform plan

# Apply changes
terraform apply

# Destroy everything (careful!)
terraform destroy
```

## Key Outputs

After `terraform apply`:

| Output | Description |
|--------|-------------|
| `staging_url` | Staging site URL (ALB hostname) |
| `ecr_repository_url` | ECR URL for Docker images |
| `rds_endpoint` | MySQL connection endpoint |
| `ecs_cluster_name` | ECS cluster name |

## SSM Parameters

| Parameter | Type | Purpose |
|-----------|------|---------|
| `/sam/{env}/db-host` | String | RDS endpoint address |
| `/sam/{env}/db-username` | SecureString | Database username |
| `/sam/{env}/db-password` | SecureString | Database password |
| `/sam/{env}/flask-secret-key` | SecureString | Flask session signing key |
| `/sam/{env}/oidc-client-id` | SecureString | Microsoft Entra OIDC client ID |
| `/sam/{env}/oidc-client-secret` | SecureString | Microsoft Entra OIDC client secret |
| `/sam/{env}/oidc-issuer` | SecureString | OIDC issuer URL |

## OIDC SSO Integration

The webapp supports OIDC SSO authentication via Microsoft Entra (Azure AD), controlled by the `AUTH_PROVIDER` environment variable:

- `AUTH_PROVIDER=stub` (default) -- Development mode, any password works
- `AUTH_PROVIDER=oidc` -- OIDC SSO via Authlib + PKCE

### Enabling OIDC in Staging

Prerequisites:
1. HTTPS listener on ALB (ACM certificate + domain required)
2. Microsoft Entra app registration from UCAR IT (see handoff checklist below)
3. SSM parameters populated with real values (replace `placeholder` defaults)

Steps:
1. Update `secrets.auto.tfvars` with real OIDC values from IT
2. Run `terraform apply` to update SSM parameters
3. Change `AUTH_PROVIDER` from `stub` to `oidc` in `ecs.tf`
4. Deploy updated task definition

### IT Handoff Checklist (for UCAR IT Help Desk)

Request the following from IT to register the SAM webapp with Microsoft Entra:

- **Tenant ID**: UCAR/NCAR Microsoft Entra tenant
- **Client ID**: Registered application client ID
- **Client Secret**: Application client secret (will be stored in SSM SecureString)
- **Redirect URI**: `https://<app-domain>/auth/oidc/callback` (must be HTTPS)
- **Required scopes**: `openid email profile`
- **Required claims**: `preferred_username`, `email`, `sub`
- **End-session endpoint URL**: For RP-initiated logout

### Rollback

To revert from OIDC to stub auth: change `AUTH_PROVIDER` back to `stub` in the ECS task definition and redeploy. No code changes needed.

## Security

- ALB only accepts traffic from UCAR network (128.117.0.0/16)
- RDS accessible from ECS containers and UCAR VPN
- Secrets stored in SSM Parameter Store (SecureString)
- `secrets.auto.tfvars` is gitignored
- OIDC client secrets are never stored in plaintext env vars -- injected via SSM

## Deployment

Deployments happen automatically via GitHub Actions when code is pushed to the `staging` branch. See `.github/workflows/deploy-staging.yaml`.

Manual deployment: `./scripts/infra/deploy-staging.sh`

## Future: DNS and HTTPS

When UCAR DNS team is ready:
1. Add Route 53 hosted zone for `sam-staging.ucar.edu`
2. Request ACM certificate
3. Update ALB listener to HTTPS
4. UCAR DNS team adds NS delegation records
