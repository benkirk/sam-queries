# Legacy API Parity Check

Standalone utility for comparing the five Systems Integration APIs on the
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
| `/api/v1/queue/`                     | `/api/protected/admin/ssg/queue`                      |
| `/api/v1/wallclock_exemption/`       | `/api/protected/admin/ssg/wallClockExemption`         |
| `/api/xras/v1/*` (6 GETs)            | `/api/xras/v1/*` — **same paths on both stacks**      |

The response schemas are specified in
[`docs/apis/SYSTEMS_INTEGRATION_APIs.md`](../../docs/apis/SYSTEMS_INTEGRATION_APIs.md).
~40 comparison rules run across the six APIs, each returning a
pass/fail `CheckResult` with sample mismatch lines.

### XRAS is the exception — byte-exact, not tolerant

`--api xras` compares **raw response bytes**, and it is the strictest check
here. The other five APIs read a *mirrored* database, so some lag is expected
and the rules are tolerant. XRAS has no such lag: both stacks read the same
production `sam` schema, and a single `api_credentials` row authenticates
against both — so no difference in what the two can *see* can be mistaken for a
difference in what they *render*. Any difference is a rendering difference and
must fail.

Byte comparison is also the only thing that catches the bugs that matter on
this surface, which are length-preserving: a swapped `firstName`/`lastName`, a
reordered field, a `"%.1f"` that drifted. A parsed comparison sees none of them.

One deliberate relaxation: the `masters[]` array of an
`AccountingRequestResponse` is emitted by legacy in Java `HashMap` bucket order
over the projcode keys — an artifact of its data structure, not of the data.
The port sorts instead, so masters are compared **byte-exact individually and
order-insensitively as a sequence**. See `docs/xras/incoming/XRAS_REIMPLEMENTATION.md`
section 7.

The sample is bootstrapped from legacy's own output rather than hardcoded: the
roster supplies a username and that user's requests supply projcodes. Because
22k of the roster's 28k entries are inactive, the search walks newest-first for
a user who actually has projects; `--xras-user` overrides it. Budget ~2 minutes
for a run — legacy spends 6-7 s on every `requests/*` call, and the roster is
3.8 MB fetched twice.

Most other rules are one-directional (*legacy ⊆ new*) to absorb DB-mirror lag.
`directory_access` additionally carries three checks in the reverse
direction — surplus group members, surplus account usernames, and a
"no dangling group members" self-consistency invariant asserted against
each payload on its own. The other four APIs are still forward-only.

## Required environment variables

```bash
SAM_LEGACY_USER     # HTTP Basic Auth username for sam.ucar.edu
SAM_LEGACY_PASS     # HTTP Basic Auth password for sam.ucar.edu
SAM_NEW_API_USER    # HTTP Basic Auth username for samuel.k8s (falls back to SAM_LEGACY_USER)
SAM_NEW_API_PASS    # HTTP Basic Auth password for samuel.k8s (falls back to SAM_LEGACY_PASS)
SAM_XRAS_USER       # ROLE_XRAS credential, valid on BOTH stacks (--api xras only)
SAM_XRAS_PASS       #   no fallback: the SAM_LEGACY_* account cannot reach /api/xras/*
```

`SAM_XRAS_*` is optional. Without it `--api all` still compares the other five
APIs and skips `xras` with a message on stderr.

> ⚠️ The XRAS credential carries `ROLE_XRAS`, and that security chain makes no
> method distinction — the same secret authorises `POST /api/xras/v1/actions`
> against **production**. Treat it as a write credential.

These are typically loaded from the shared `.env`:

```bash
source etc/config_env.sh
```

## Usage

```bash
# Full comparison across all five APIs
python utils/parity/check_legacy_apis.py

# One API at a time
python utils/parity/check_legacy_apis.py --api directory
python utils/parity/check_legacy_apis.py --api project --branch hpc
python utils/parity/check_legacy_apis.py --api fstree --resource Derecho
python utils/parity/check_legacy_apis.py --api queue
python utils/parity/check_legacy_apis.py --api wallclock
python utils/parity/check_legacy_apis.py --api xras
python utils/parity/check_legacy_apis.py --api xras --xras-user benkirk -v

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
- **None at all** for `--api xras` — see above.
