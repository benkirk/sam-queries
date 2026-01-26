# Credentials Configuration Guide

Guide for configuring credentials in your local `.env` file.

## Important Security Notes

⚠️ **Never commit `.env` file to Git** - It's already in `.gitignore`  
⚠️ **Never share credentials** - Keep them secure  
⚠️ **Rotate credentials** - If compromised, contact appropriate staff immediately

## Setting Up Credentials

### Step 1: Copy Example File

```bash
cp .env.example .env
chmod 600 .env  # Secure permissions
```

### Step 2: Add Your Credentials

Edit `.env` file and add your actual credentials (see sections below).

## Database Credentials

### Production Database (Read-Only)

**Where to get:** Contact CISL staff or your project lead

**Example format:**
```bash
PROD_SAM_DB_USERNAME=hpc-reader
PROD_SAM_DB_SERVER=sam-sql.ucar.edu
PROD_SAM_DB_PASSWORD='your_password_here'
```

**Important:** Wrap passwords with special characters in single quotes:
```bash
PROD_SAM_DB_PASSWORD='password$with!special#chars'  # ✅ Correct
PROD_SAM_DB_PASSWORD=password$with!special#chars    # ❌ Will fail
```

**Access:** Read-only (safe for queries, CLI, Python REPL)

### Local Database

**Default credentials** (already configured):
```bash
LOCAL_SAM_DB_USERNAME=root
LOCAL_SAM_DB_SERVER=127.0.0.1
LOCAL_SAM_DB_PASSWORD=root
```

**Note:** These are for local Docker container only.

## GitHub Personal Access Token

**Where to create:**
1. Go to: https://github.com/settings/tokens
2. Click "Generate new token" → "Generate new token (classic)"
3. Select scopes: `repo` (full control of private repositories)
4. Copy token immediately (you won't see it again)

**Add to .env:**
```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_your_token_here
```

**Used for:** GitHub MCP Server integration in Cursor

## AWS Credentials

**Where to get:** AWS Console → IAM → Security Credentials

**Add to .env:**
```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_SESSION_TOKEN=your_session_token_if_needed  # Optional, for temporary credentials
```

**Alternative:** Can use AWS CLI default profile instead:
```bash
aws configure  # Sets up default profile
# Then AWS MCP Server will use default profile automatically
```

**Used for:** AWS MCP Server integration in Cursor

## JupyterHub API Token

**Where to get:** Contact JupyterHub administrators or check JupyterHub settings

**Add to .env:**
```bash
JUPYTERHUB_API_TOKEN=your_jupyterhub_token
```

**Used for:** Real-time JupyterHub statistics in webapp

## Credential Reference

| Credential | Required | Where to Get | Used For |
|------------|----------|--------------|----------|
| `PROD_SAM_DB_*` | Optional | CISL staff | Production database access |
| `LOCAL_SAM_DB_*` | Auto-set | Docker container | Local database (default) |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Optional | GitHub Settings | GitHub MCP Server |
| `AWS_*` | Optional | AWS Console | AWS MCP Server |
| `JUPYTERHUB_API_TOKEN` | Optional | JupyterHub admin | JupyterHub stats |

## Verifying Credentials

### Test Database Connection

```bash
source etc/config_env.sh
sam-search user benkirk  # Should work if database credentials are correct
```

### Test GitHub Token

```bash
# Token format should be: ghp_...
echo $GITHUB_PERSONAL_ACCESS_TOKEN | grep -q "^ghp_" && echo "✅ Token format looks correct"
```

### Test AWS Credentials

```bash
# If using AWS CLI
aws sts get-caller-identity  # Should show your AWS account info
```

## Troubleshooting

### Issue: "Access denied" for database

**Check:**
1. Credentials are correct in `.env`
2. Password wrapped in single quotes (if special characters)
3. VPN connected (if required for production)
4. SSL setting matches database requirement

### Issue: GitHub token not working

**Check:**
1. Token has `repo` scope
2. Token hasn't expired
3. Token format: `ghp_...` (starts with `ghp_`)

### Issue: AWS credentials not working

**Check:**
1. Credentials are active (not expired)
2. IAM user has necessary permissions
3. Region matches your resources
4. If using session token, it hasn't expired

## Rotating Credentials

If credentials are compromised:

1. **Database:** Contact CISL staff immediately
2. **GitHub:** Revoke token at https://github.com/settings/tokens
3. **AWS:** Rotate access keys in AWS Console → IAM
4. **Update `.env`:** Replace with new credentials

## See Also

- [LOCAL_SETUP.md](LOCAL_SETUP.md) - Complete setup guide
- [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md) - Switching databases
- [README.md](../README.md) - Project overview
