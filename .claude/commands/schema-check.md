---
description: Validate ORM models against database schema to detect drift
---

# Schema Check Command

Run schema validation tests to ensure ORM models match the actual database schema. This catches issues like missing columns, wrong types, or primary key mismatches.

## What It Validates

- All ORM tables exist in database
- All ORM columns exist with correct types
- Primary key configurations match
- Foreign key relationships are valid
- Coverage metrics (current: 94% of tables have ORM models)

## Execution

1. Change to tests directory: `/Users/benkirk/codes/sam-queries/tests`
2. Run: `python -m pytest integration/test_schema_validation.py -v --tb=short`
3. Report any schema mismatches found

## When To Use

- After adding or modifying ORM models in `python/sam/`
- After database schema changes
- Before committing model changes
- When queries return unexpected results

## Common Issues Detected

- **Missing columns**: ORM model has column not in DB (or vice versa)
- **Type mismatches**: SQLAlchemy type doesn't match MySQL type
- **Primary key errors**: Single vs composite PK mismatch (e.g., DavActivity)
- **Missing tables**: ORM model for non-existent table

## Example Output

```
test_all_orm_tables_exist_in_database PASSED
test_all_orm_columns_exist_in_database PASSED
test_column_types_are_compatible PASSED
test_primary_keys_match PASSED
...
18 passed in 5.23s
```
