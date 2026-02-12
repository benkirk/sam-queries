# Docker Troubleshooting Guide

Common Docker issues and solutions for SAM Queries development.

## Permission Denied Error

### Error Message
```
permission denied while trying to connect to the Docker daemon socket
at unix:///Users/username/.docker/run/docker.sock
```

### Solutions

#### 1. Check Docker Desktop Status

```bash
# Check if Docker Desktop is running
./scripts/setup/check_docker.sh

# Or manually check
pgrep -f "Docker Desktop"
```

**Fix:** Start Docker Desktop application and wait for it to fully start (whale icon in menu bar).

#### 2. Restart Docker Desktop

1. Quit Docker Desktop completely (right-click whale icon → Quit)
2. Wait 10 seconds
3. Start Docker Desktop again
4. Wait for it to fully start
5. Try your command again

#### 3. Check Docker Socket Permissions

```bash
# Check socket location and permissions
ls -l ~/.docker/run/docker.sock

# If socket exists but has wrong permissions, Docker Desktop should fix this
# If not, restart Docker Desktop
```

#### 4. Cursor-Specific Issues

If Docker works in regular Terminal but not in Cursor:

**Option A: Restart Cursor**
- Quit Cursor completely
- Restart Cursor
- Try again

**Option B: Use Regular Terminal**
- Use Terminal.app or iTerm2 for Docker commands
- Cursor's integrated terminal may have permission restrictions

**Option C: Check Cursor Settings**
- Cursor → Settings → Search "terminal"
- Check terminal integration settings
- May need to grant Cursor full disk access

#### 5. Grant Full Disk Access (macOS)

1. System Settings → Privacy & Security → Full Disk Access
2. Add Cursor (if not already added)
3. Restart Cursor

#### 6. Reset Docker Desktop

If nothing else works:

1. Quit Docker Desktop
2. Reset Docker Desktop:
   ```bash
   # Remove Docker socket (will be recreated)
   rm -rf ~/.docker/run
   ```
3. Start Docker Desktop
4. Wait for full startup

## Alternative: Use Regular Terminal

If Cursor's terminal continues to have issues, use a regular terminal for Docker commands:

```bash
# In Terminal.app or iTerm2
cd /Users/metzger/Github/sam-queries
docker compose ps
docker compose logs webapp
docker compose up -d
```

## Verify Docker Works

Run the diagnostic script:

```bash
./scripts/setup/check_docker.sh
```

This will check:
- Docker Desktop process status
- Socket location and permissions
- Docker connection
- docker compose availability

## Common Commands That Work Around Issues

Instead of `docker compose ps`, try:

```bash
# Direct docker command
docker ps

# With filters
docker ps --filter "label=com.docker.compose.project=sam-queries"

# Check specific container
docker ps --filter "name=samuel-mysql"
docker ps --filter "name=samuel-webapp"
```

## Still Having Issues?

1. **Check Docker Desktop logs:**
   - Docker Desktop → Troubleshoot → View logs

2. **Check system logs:**
   ```bash
   log show --predicate 'process == "com.docker.backend"' --last 5m
   ```

3. **Verify Docker Desktop version:**
   ```bash
   docker --version
   docker compose version
   ```

4. **Try Docker Desktop reset:**
   - Docker Desktop → Settings → Troubleshoot → Reset to factory defaults
   - ⚠️ This will remove all containers and volumes

## Prevention

- Always ensure Docker Desktop is fully started before running commands
- Use `./scripts/setup/check_docker.sh` to verify Docker is ready
- Keep Docker Desktop updated
- Restart Docker Desktop if you see permission errors

## See Also

- [LOCAL_SETUP.md](LOCAL_SETUP.md) - Local development setup
- [WEBAPP_SETUP.md](WEBAPP_SETUP.md) - Web application setup
