# SAM API demo

Worked examples of calling SAM's HTTP APIs, each written twice: once as
**bash** (`curl` + `jq`) and once as **Python** (standard library only). Every
example is a full, copy-pasteable request with a one-line comment saying what
it shows. The same scripts run in SAM's CI as a smoke test, so they track the
real API.

The directory stands alone: copy it or `tar` it and share it. Its only
dependencies are `bash`, `curl`, `jq` and `python3`.

## Setup

1. Get an API key: a key name and its secret. SAM operators issue them.
2. Copy `sam.env.example` to `sam.env` and fill in `SAM_API_BASE`,
   `SAM_API_USER` and `SAM_API_PASS`. Exporting the same variables works too.
   Keep `sam.env` private.
3. Run one example, or all of them:

   ```bash
   rest/projects.sh               # one family member, bash
   python3 rest/projects.py       # the same, Python
   ./run_all.sh                   # everything, pass/fail per script
   ./run_all.sh -v rest           # one family, with its output
   ```

## Layout

The directories follow how SAM's own docs group the endpoints.

| Directory | Endpoints | Key needs |
|---|---|---|
| `rest/` | `/api/v1`: health, users, projects, allocations, charges, status, admin cache refresh | a grant per endpoint (below) |
| `systems-integration/` | `/api/v1`: directory_access, project_access, fstree_access, queue, wallclock_exemption, disk_quota | the `api_systems_integration` role |
| `ldapsync/` | `/api/protected/admin/ldapsync`: the identity-sync feed | legacy role `ROLE_API_ADMIN` |
| `xras/` | `/api/xras/v1`: the endpoints NSF's XRAS calls | legacy role `ROLE_XRAS` |

`lib/connect.sh` and `lib/sam_api.py` only read the variables, check that
`curl` and `jq` are on the path, and (Python) hold the two-line request helper.
Every example is written out in full in its own script.

## How a key is authorized

Requests use HTTP Basic auth: the key name as the username, the secret as the
password.

- **Production and dev** check two things, depending on the family:
  - `rest/` and `systems-integration/`: the key needs the route's permission
    through an explicit grant (`sam-admin rbac --grant apikey:NAME ...`). A key
    with no grant gets 403 everywhere except health and status.
  - `ldapsync/` and `xras/`: a role on the key itself, in SAM's
    `api_credentials` table. A key set in the webapp's config (`API_KEYS_<NAME>`)
    carries no such role, so these two answer 401 or 403 to it.
- **A local server** (`RBAC_SOURCE=defaults`) admits any valid key on the first
  two families. To add one, set `API_KEYS_<NAME>=<bcrypt hash>` in the webapp's
  `.env`; `scripts/gen_api_key.py` in the SAM repo makes the pair.

## What changes data

Almost everything here reads. The writes:

- `rest/cache_refresh.*` clears SAM's server-side caches, once for all of them
  and then once per category. Caches are derived data, so nothing is lost, but
  pages run slower until they warm again. It needs a key that holds
  `SYSTEM_ADMIN`.
- Each `systems-integration/*` script ends with that API's `POST .../refresh`,
  which drops its 5-minute response cache.

`SAM_API_BASE` has no default, so a shared copy never refreshes a production
cache by accident.

## Not covered

- `/api/v1/fairshare/<machine>` and `/api/v1/projects/<projcode>/jobs`: they need
  the hpc-usage-queries plugin, which not every deployment loads.
- Writes beyond cache refresh: project-member changes, allocation edits,
  status ingest, charge-summary ingest, and the XRAS and LDAP sync writes.
- `/api/v1/users/me`: a key is not a person, so it has no "me".

## Smoke test

`./run_all.sh` exits non-zero when any example fails. An example fails when a
call returns an HTTP error, or a response lacks a field the example reads
(`jq` errors on iterating a missing list; Python raises `KeyError`). SAM's CI
runs it against a freshly started stack on every pull request.
