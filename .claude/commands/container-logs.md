---
description: View logs from the MySQL development container
---

# Container Logs

View and stream logs from the MySQL development container for debugging.

## Execution

1. Change to container directory: `/Users/benkirk/codes/sam-queries/containers/sam-sql-dev`
2. Run: `docker-compose logs` (or with options below)

## Options

- `docker-compose logs` - Show all logs
- `docker-compose logs -f` - Follow/stream logs continuously
- `docker-compose logs --tail 50` - Show last 50 lines
- `docker-compose logs -f --tail 100` - Stream starting from last 100 lines

## What To Look For

### Healthy Startup
```
mysqld: ready for connections.
Version: '8.0.x'  socket: '/var/run/mysqld/mysqld.sock'  port: 3306
```

### Common Issues

**Connection refused errors**:
```
Can't connect to MySQL server on '127.0.0.1:3306'
```
→ Container not fully started, wait a few seconds

**Authentication errors**:
```
Access denied for user 'root'@'172.x.x.x'
```
→ Check password in connection string

**Table doesn't exist**:
```
Table 'sam.tablename' doesn't exist
```
→ Database dump may not have loaded; try `/db-restore`

## Exit Streaming

Press `Ctrl+C` to stop following logs.
