---
description: Start the MySQL development container with anonymized SAM data
---

# Start Development Containers

Start the MySQL development container pre-loaded with anonymized SAM database.

## Execution

1. Change to container directory: `/Users/benkirk/codes/sam-queries/containers/sam-sql-dev`
2. Run: `docker-compose up -d`
3. Wait for MySQL to be ready (check logs if needed)
4. Report connection details

## Connection Details

```
Host: 127.0.0.1
Port: 3306
User: root
Password: root
Database: sam
```

## Test Connection

```bash
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT COUNT(*) FROM users;"
```

## What's Included

The container includes an anonymized copy of the SAM database with:
- ~97 tables with production-like schema
- Anonymized user data (no real names/emails)
- Representative allocation and charge data
- All relationships preserved

## Container Management

- View logs: `docker-compose logs -f`
- Stop: `docker-compose down`
- Reset data: `docker-compose down -v && docker-compose up -d`

## Troubleshooting

- **Port 3306 in use**: Stop local MySQL or change port in docker-compose.yaml
- **Container won't start**: Check `docker-compose logs` for errors
- **Slow startup**: First run loads full database dump (~1-2 minutes)
