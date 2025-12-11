---
description: Execute database queries using sam_search CLI or direct ORM
---

# Database Query

Execute queries against the SAM database using the sam_search CLI tool or direct ORM queries.

## Using sam_search CLI

The `sam_search.py` CLI provides common query patterns:

### User Queries
```bash
./python/sam_search.py user <username>
./python/sam_search.py user <username> --list-projects --verbose
./python/sam_search.py user --search "pattern%"
./python/sam_search.py user --abandoned
./python/sam_search.py user --has-active-project
```

### Project Queries
```bash
./python/sam_search.py project <projcode>
./python/sam_search.py project <projcode> --list-users --verbose
./python/sam_search.py project --search "SCSG%"
./python/sam_search.py project --upcoming-expirations
./python/sam_search.py project --recent-expirations
```

## Direct MySQL Queries

For ad-hoc SQL:
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "YOUR QUERY HERE"
```

## Common Queries

### Count records
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT COUNT(*) FROM users;"
```

### Find user by pattern
```bash
./python/sam_search.py user --search "ben%"
```

### Project allocation details
```bash
./python/sam_search.py project SCSG0001 --verbose
```

### Expiring allocations
```bash
./python/sam_search.py project --upcoming-expirations --list-users
```

## Prerequisites

- MySQL container must be running (`/container-up`)
- For sam_search: Run from project root directory
