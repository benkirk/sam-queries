# Docker MySQL Container - Backup Restore

## Usage

### Default Mode (No Restore)
Start container with empty `sam` database:
```bash
./docker_start.sh
```

### Restore Mode
Start container and restore from backup:
```bash
./docker_start.sh backups/sam-obfuscated.sql.xz
```

## How It Works

**Default Mode:**
- Creates fresh MySQL container
- Initializes empty `sam` database
- Persists data in `local-sam-mysql-vol` volume
- Wait time: 20 seconds

**Restore Mode:**
- Removes existing volume (required for restore)
- Mounts backup file into `/docker-entrypoint-initdb.d/`
- MySQL automatically restores on first startup
- Wait time: 60 seconds (larger backups may need more)

## Supported Backup Formats

The MySQL container automatically handles:
- `.sql` - Plain SQL dump
- `.sql.gz` - Gzip compressed
- `.sql.xz` - XZ compressed (like your backups)

## Examples

```bash
# Start fresh container
./docker_start.sh

# Restore from specific backup
./docker_start.sh backups/sam-obfuscated.sql.xz

# Restore from relative path
./docker_start.sh ../backups/sam-2025-11-16.sql.xz

# Restore from absolute path
./docker_start.sh /Users/benkirk/backups/sam.sql.xz
```

## Important Notes

⚠️ **Volume Removal**: When restoring from backup, the existing volume is removed to ensure a clean restore. This is required because MySQL's `/docker-entrypoint-initdb.d` only runs when the data directory is empty.

⏱️ **Wait Time**: Restore mode uses 60-second wait. For very large backups, you may need to increase this:
```bash
# Edit docker_start.sh and change line 37:
waittime=120  # Increase for large backups
```

🔍 **Verification**: After restore, verify the data:
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT COUNT(*) FROM users"
```

## Troubleshooting

**Backup not found:**
```
❌ Error: Backup file not found: backups/sam-obfuscated.sql.xz
```
→ Check file path is correct

**Container still initializing:**
```
ERROR 2003 (HY000): Can't connect to MySQL server
```
→ Wait longer or increase `waittime`

**Restore taking too long:**
→ Check container logs: `docker logs local-sam-mysql`

## Integration with Makefile

You can add restore targets to your Makefile:
```makefile
.PHONY: restore-obfuscated
restore-obfuscated:
	./docker_start.sh backups/sam-obfuscated.sql.xz

.PHONY: restore-original
restore-original:
	./docker_start.sh backups/sam-original.sql.xz
```

Then run:
```bash
make restore-obfuscated
```
