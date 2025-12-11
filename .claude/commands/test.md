---
description: Run the pytest test suite with optional filtering
---

# Test Command

Run the SAM Queries pytest test suite. Supports filtering by test type or pattern.

## Arguments

- No args: Run full test suite
- `unit`: Run only unit tests
- `api`: Run only API tests
- `integration`: Run only integration tests
- `schema`: Run schema validation tests
- `-k "pattern"`: Run tests matching pattern

## Execution

1. Change to the `tests/` directory: `/Users/benkirk/codes/sam-queries/tests`
2. Run pytest with appropriate arguments:
   - Full suite: `python -m pytest -v --tb=short`
   - Filtered: `python -m pytest -v --tb=short <path_or_args>`
3. Report summary of results

## Test Locations

- Unit tests: `tests/unit/`
- API tests: `tests/api/`
- Integration tests: `tests/integration/`
- Schema validation: `tests/integration/test_schema_validation.py`

## Expected Results

Current baseline: ~302 passed, 12 skipped, 2 xpassed in ~65 seconds

## Example Usage

```
/test                    # Full suite
/test unit               # Unit tests only
/test api                # API tests only
/test schema             # Schema validation
/test -k "user"          # Tests with "user" in name
```
