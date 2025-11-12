# MySQL Mock Clone Utility

This toolkit creates a lightweight **local MySQL clone** of a large remote database:
- Copies full schema
- Copies all small tables
- Samples large tables (most recent rows)
- Cleans up dangling relationships

## Requirements
- Docker
- Python 3.8+
- `PyMySQL`, `PyYAML` installed locally
  ```bash
  pip install PyMySQL PyYAML
