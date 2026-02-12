# Web Application Setup

Guide for running the SAM Queries web application locally.

## Prerequisites

- Local database must be running (see [LOCAL_SETUP.md](LOCAL_SETUP.md))
- Docker Desktop must be running

## Quick Start

```bash
# Ensure database is running
docker compose ps mysql

# Start web application
docker compose up webapp

# Or start both database and webapp
docker compose up
```

Access the application at: **http://localhost:5050**

## Development Mode

The webapp runs in development mode by default with:
- Auth disabled (`DISABLE_AUTH=1`)
- Auto-login as `benkirk` (`DEV_AUTO_LOGIN_USER=benkirk`)
- Flask debug mode enabled
- Hot-reload enabled (code changes sync automatically)

## Building the Container

If you need to rebuild the webapp container:

```bash
# Rebuild webapp container
docker compose build webapp

# Or rebuild everything
docker compose build
```

## Troubleshooting

### Issue: "unknown flag: --exclude"

**Solution:** This has been fixed. The Dockerfile now uses `.dockerignore` instead of `COPY --exclude`. If you still see this error:

1. Ensure you have the latest code
2. Rebuild the container: `docker compose build webapp`

### Issue: "Cannot connect to database"

**Solution:**
1. Ensure MySQL container is running: `docker compose ps mysql`
2. Wait for it to be healthy
3. Check database connection: `./test_database.sh`

### Issue: Container fails to start

**Solution:**
1. Check logs: `docker compose logs webapp`
2. Check if port 5050 is already in use: `lsof -i :5050`
3. Rebuild container: `docker compose build webapp`

### Issue: Permission errors during build

**Solution:**
1. Ensure Docker Desktop has proper permissions
2. Try: `docker compose build --no-cache webapp`
3. Restart Docker Desktop if needed

## Container Details

- **Image:** Built from `containers/webapp/Dockerfile`
- **Port:** 5050
- **Network:** sam-network (connects to MySQL container)
- **Volumes:** 
  - `./logs` → `/var/log/sam` (application logs)
  - Code is synced via Docker Compose watch feature

## Environment Variables

Set in `compose.yaml`:
- `DISABLE_AUTH=1` - Disable authentication (dev mode)
- `DEV_AUTO_LOGIN_USER=benkirk` - Auto-login user
- `FLASK_DEBUG=1` - Enable Flask debug mode
- `PYTHONDONTWRITEBYTECODE=1` - Disable .pyc files
- `AUDIT_ENABLED=1` - Enable audit logging

## Stopping the Application

```bash
# Stop webapp only
docker compose stop webapp

# Stop everything (webapp + database)
docker compose down
```

## Production Deployment

For production, you'll need to:
1. Remove `DISABLE_AUTH=1`
2. Set proper authentication
3. Disable Flask debug mode
4. Configure proper SSL/TLS
5. Set up proper logging

See `compose.yaml` for production configuration options.
