# Docker Compose CI Setup

## Overview

This setup allows you to:
1. **Start MySQL in Docker Compose** with automatic backup restore
2. **Wait for healthy status** (MySQL ready + restore complete)
3. **Connect from host** (GitHub Actions runner) to run tests/queries

## Files

```
containers/sam-sql-dev/
├── docker-compose.yaml        # Base configuration
├── docker-compose.ci.yaml     # CI override (mounts backup)
├── backups/
│   └── sam-fixed.sql.xz      # Anonymized backup
└── .github/workflows/
    └── sam-ci.yaml            # GitHub Actions workflow
```

## Local Usage

### Start with backup restore
```bash
cd containers/sam-sql-dev

# Start MySQL and restore from backup
docker compose -f docker-compose.yaml -f docker-compose.ci.yaml up -d

# Wait for healthy status
docker compose ps

# Connect from host
mysql -h 127.0.0.1 -u root -proot sam
```

### Start fresh (no restore)
```bash
# Just base config - no backup mounted
docker compose up -d

# Connect
mysql -h 127.0.0.1 -u root -proot sam
```

### Check health status
```bash
# View service status
docker compose ps

# View logs
docker compose logs -f mysql

# Check health manually
docker exec sam-mysql-ci mysqladmin ping -h localhost -u root -proot
```

### Cleanup
```bash
# Stop and remove containers + volumes
docker compose down -v

# Or just stop (keep volumes)
docker compose down
```

## GitHub Actions CI

### How It Works

**Step 1: Start Services**
```yaml
- name: Start MySQL with backup restore
  run: docker compose -f docker-compose.yaml -f docker-compose.ci.yaml up -d
```

**Step 2: Wait for Healthy**
```yaml
- name: Wait for MySQL to be healthy
  run: |
    timeout 300 bash -c '
      until docker compose ps | grep -q "healthy"; do
        sleep 5
      done
    '
```

**Step 3: Connect from Runner**
```yaml
- name: Install MySQL client
  run: sudo apt-get install -y mysql-client

- name: Run queries
  run: mysql -h 127.0.0.1 -u root -proot sam -e "SELECT * FROM users LIMIT 5"
```

### Health Check Details

The health check verifies TWO conditions:

1. **MySQL accepting connections**:
   ```bash
   mysqladmin ping -h localhost -u root -proot
   ```

2. **Restore complete** (users table exists):
   ```bash
   mysql -u root -proot sam -e "SELECT COUNT(*) FROM users"
   ```

**Timing:**
- `start_period: 90s` - Give MySQL time to restore before checking
- `interval: 10s` - Check every 10 seconds (5s in CI)
- `retries: 30` - Try up to 30 times (60 in CI)
- **Max wait**: 90s + (30 × 10s) = ~6 minutes

## Configuration Options

### Custom Backup File

Edit `docker-compose.ci.yaml`:
```yaml
volumes:
  - ./backups/my-custom-backup.sql.xz:/docker-entrypoint-initdb.d/backup.sql.xz:ro
```

### Custom Health Check

Edit `docker-compose.yaml`:
```yaml
healthcheck:
  test: |
    mysqladmin ping -h localhost -u root -proot &&
    mysql -u root -proot sam -e "SELECT COUNT(*) FROM my_table"
  interval: 10s
  retries: 30
```

### Longer Restore Times

For larger backups, increase timing:
```yaml
healthcheck:
  start_period: 180s  # 3 minutes before first check
  interval: 15s       # Check every 15 seconds
  retries: 60         # Try 60 times = 15 minutes max
```

## GitHub Actions Backup Storage

### Option 1: Store in Repository
```yaml
# Commit backup to repo (if <100MB)
git add backups/sam-fixed.sql.xz
git commit -m "Add CI backup"

# Workflow uses it directly
- name: Verify backup
  run: ls -lh backups/sam-fixed.sql.xz
```

**Pros**: Simple, versioned with code
**Cons**: Increases repo size, 100MB GitHub limit

### Option 2: Use GitHub Artifacts
```yaml
# Upload backup as artifact (separate workflow)
- uses: actions/upload-artifact@v4
  with:
    name: sam-backup
    path: backups/sam-fixed.sql.xz
    retention-days: 30

# Download in CI workflow
- uses: actions/download-artifact@v4
  with:
    name: sam-backup
    path: containers/sam-sql-dev/backups/
```

**Pros**: Doesn't bloat repo
**Cons**: Manual upload, retention limits

### Option 3: Use External Storage
```yaml
# Download from S3/GCS/Azure
- name: Download backup
  run: |
    aws s3 cp s3://my-bucket/sam-fixed.sql.xz backups/
    # or
    gsutil cp gs://my-bucket/sam-fixed.sql.xz backups/
```

**Pros**: Flexible, no size limits
**Cons**: Requires cloud credentials

## Troubleshooting

### Container never becomes healthy
```bash
# Check logs
docker compose logs mysql

# Check if restore is running
docker exec sam-mysql-ci ps aux | grep mysql

# Manually test health check
docker exec sam-mysql-ci mysqladmin ping -h localhost -u root -proot
docker exec sam-mysql-ci mysql -u root -proot sam -e "SELECT COUNT(*) FROM users"
```

### Connection refused from host
```bash
# Check port mapping
docker compose ps
# Should show: 0.0.0.0:3306->3306/tcp

# Test connection
telnet localhost 3306

# Check firewall
sudo ufw status
```

### Restore taking too long
```bash
# Monitor restore progress in logs
docker compose logs -f mysql | grep -E "ready|restore|import"

# Check disk I/O
docker stats sam-mysql-ci

# Increase health check timing
# Edit docker-compose.ci.yaml and increase start_period
```

### Health check passes but queries fail
```bash
# The restore might still be running
# Wait a bit longer or adjust health check query

# Try a simpler check
healthcheck:
  test: mysqladmin ping -h localhost -u root -proot
```

## Example: Complete CI Run

```bash
# 1. Start services
$ docker compose -f docker-compose.yaml -f docker-compose.ci.yaml up -d
Creating network "sam-sql-dev_sam-network" ... done
Creating sam-mysql-ci ... done

# 2. Wait for healthy (takes ~90-120 seconds)
$ docker compose ps
NAME            STATUS                    PORTS
sam-mysql-ci    Up 2 minutes (healthy)    0.0.0.0:3306->3306/tcp

# 3. Connect from host
$ mysql -h 127.0.0.1 -u root -proot sam -e "SELECT COUNT(*) FROM users"
+----------+
| COUNT(*) |
+----------+
|    27213 |
+----------+

# 4. Run tests
$ python3 check_username_leak.py
✓ 0 non-anonymized usernames found

# 5. Cleanup
$ docker compose down -v
Stopping sam-mysql-ci ... done
Removing sam-mysql-ci ... done
Removing network sam-sql-dev_sam-network
Removing volume sam-sql-dev_mysql-data
```

## Integration with Makefile

Add targets for CI workflows:

```makefile
.PHONY: ci-up
ci-up:
	docker compose -f docker-compose.yaml -f docker-compose.ci.yaml up -d
	@echo "Waiting for healthy status..."
	@timeout 300 bash -c 'until docker compose ps | grep -q "healthy"; do sleep 5; done'
	@echo "✅ MySQL ready!"

.PHONY: ci-test
ci-test: ci-up
	python3 check_username_leak.py
	python3 test_username_anonymization.py

.PHONY: ci-down
ci-down:
	docker compose -f docker-compose.yaml -f docker-compose.ci.yaml down -v
```

Usage:
```bash
make ci-up      # Start and wait
make ci-test    # Run tests
make ci-down    # Cleanup
```

## Best Practices

1. **Keep backups small** - Only include necessary data for CI
2. **Use .xz compression** - Smaller, faster restores
3. **Verify health checks** - Test locally before pushing to CI
4. **Set reasonable timeouts** - Balance speed vs reliability
5. **Clean up volumes** - Use `down -v` to avoid stale data
6. **Monitor logs** - Check restore progress in CI runs
7. **Version backups** - Tag backups with dates/commits

## Security Notes

⚠️ **For CI/CD**:
- ✅ Use anonymized/obfuscated backups (like sam-fixed.sql.xz)
- ✅ Never commit production backups to repository
- ✅ Use environment variables for sensitive credentials
- ✅ Consider using GitHub Secrets for backup URLs
- ✅ Rotate test database passwords regularly

## Performance

**Restore times** (sam-fixed.sql.xz = 9.8MB compressed):
- Local SSD: ~30-40 seconds
- GitHub Actions: ~60-90 seconds
- Self-hosted runners: varies by hardware

**Optimization tips**:
- Use `--skip-extended-insert` for faster restores (larger file)
- Consider splitting large backups into schema + data
- Use MySQL 8+ for faster restore performance
- Pre-warm Docker image cache in CI
