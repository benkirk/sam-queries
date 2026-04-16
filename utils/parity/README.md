# Legacy API Parity Check

Standalone utility for comparing the three Systems Integration APIs on the
deployed Python stack (samuel.k8s.ucar.edu) against their legacy Java
counterparts (sam.ucar.edu).

This is **not** a pytest test — it is an operations aid. It hits live
production hosts and therefore requires the UCAR VPN.

## What it compares

| New (samuel.k8s)                     | Legacy (sam.ucar.edu)                                 |
|--------------------------------------|-------------------------------------------------------|
| `/api/v1/directory_access/`          | `/api/protected/admin/sysacct/directoryaccess`        |
| `/api/v1/project_access/`            | `/api/protected/admin/sysacct/groupstatus/{branch}`   |
| `/api/v1/fstree_access/`             | `/api/protected/admin/ssg/fairShareTree/v3/{resource}`|

The response schemas are specified in
[`docs/apis/SYSTEMS_INTEGRATION_APIs.md`](../../docs/apis/SYSTEMS_INTEGRATION_APIs.md).
~28 comparison rules run across the three APIs, each returning a
pass/fail `CheckResult` with sample mismatch lines.

## Required environment variables

```bash
SAM_LEGACY_USER     # HTTP Basic Auth username for sam.ucar.edu
SAM_LEGACY_PASS     # HTTP Basic Auth password for sam.ucar.edu
SAM_NEW_API_USER    # HTTP Basic Auth username for samuel.k8s (falls back to SAM_LEGACY_USER)
SAM_NEW_API_PASS    # HTTP Basic Auth password for samuel.k8s (falls back to SAM_LEGACY_PASS)
```

These are typically loaded from the shared `.env`:

```bash
source etc/config_env.sh
```

## Usage

```bash
# Full comparison across all three APIs
python utils/parity/check_legacy_apis.py

# One API at a time
python utils/parity/check_legacy_apis.py --api directory
python utils/parity/check_legacy_apis.py --api project --branch hpc
python utils/parity/check_legacy_apis.py --api fstree --resource Derecho

# JSON output for downstream tooling
python utils/parity/check_legacy_apis.py --format json | jq .

# Verbose progress to stderr
python utils/parity/check_legacy_apis.py -v
```

## Exit codes

| Code | Meaning                                                        |
|------|----------------------------------------------------------------|
| 0    | Full parity — every check passed                               |
| 1    | At least one comparison failed (mismatches found)              |
| 2    | Precondition error (missing env var, unreachable host)         |
| 130  | Keyboard interrupt                                             |

## Why it's not in `tests/`

The pytest suite's safety guard (`tests/conftest.py`) refuses any database
other than the isolated `mysql-test` container. Running checks that need
live production data would require weakening that guard. Keeping this
utility outside `tests/` and outside pytest avoids that risk entirely — it
is a plain Python script that pytest will never collect.

For the broader test-suite architecture, see
[`docs/TESTING.md`](../../docs/TESTING.md).

## Tolerances

Comparison tolerances (lifted from the retired
`test_legacy_api_parity.py`):

- **±1 day** on allocation end dates — legacy rounds to the first of the
  following month; new stores the actual last day.
- **±5%** (or ±500 AU floor) on `adjustedUsage` — absorbs DB-mirror sync
  lag between sam.ucar.edu and the SAM database.
- **≤10 items missing** on subset checks (usernames, group names, project
  codes) — same DB-mirror lag.
- **≤5 items** on DEAD/live status inconsistencies.
- **≤3 users missing** per project+resource node in fstree.
