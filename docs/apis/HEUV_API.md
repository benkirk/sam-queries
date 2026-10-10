# HEUV API

The researcher portal's feed ("High End User View"), ported from legacy Java SAM at the same
path, auth and bytes, so a caller repoints by changing the host only (`sam.ucar.edu` ->
`sam.hpc.ucar.edu`). Read-only. Design record, evidence and parity results:
[`docs/plans/HEUV_API_PORT.md`](../plans/HEUV_API_PORT.md).

**Base URL**: `/api/protected/heuv/v1`
**Auth**: HTTP Basic, a DB `api_credentials` key holding `ROLE_API_HEUV` (legacy's Spring role).
No key: 401 with `WWW-Authenticate: Basic realm="Realm"`. A key without the role: 403. A browser
session alone is not enough.
**Rate limit**: the authed tier (default 200/min). A 429 carries the app-wide limiter body.
**Source**: `src/webapp/api/heuv/`, `src/sam/queries/heuv.py`, `src/sam/schemas/heuv.py`,
`src/sam/queries/account_status.py`

## Wire rules (all routes)

| | |
|---|---|
| Body | compact JSON in legacy's key order, nulls included, `Content-Type: application/json` |
| 400 | `{"errorMessage":"<javaMethod>.<param>: <text>"}`; the text echoes the value **as sent**: `Username X does not exist.`, `Project X does not exist.`, `Project X is not active.`, `Resource X does not exist.` |
| 404 | only `access/resource/{r}` with no branch match: empty body, no content type |
| 500 | `{"errorMessage":"Internal server error."}`; never driver text |
| Lookups | username, projcode and resource name are case-insensitive; responses carry the stored spelling |
| Dates | `yyyy-MM-dd` (Mountain wall date); the two `assigned*` routes render `yyyy-MM-dd HH:mm:ss` in **UTC** |
| Numbers | integers rounded half-up (Java `Math.round`) |

## Routes

| Route | Returns | Notes |
|---|---|---|
| `GET user/{u}/defaultproject` | `[{username, resourceName, projcode}]` | every `default_project` row, by resource |
| `GET user/{u}/group` | `[{username, groupName, unixGid, primary, project, projcode}]` | `ncar`/1000 first, then by name; an inactive user gets `ncar` only |
| `GET user/{u}/access` | `[{username, resourceName, resourceType, homeDirectory, shellName}]` | login resources from the user's configurable memberships' access branches; home and shell overrides applied |
| `GET user/{u}/assignedproject?thresholdlimited=&resourcename=` | `[{projcode, primary, title, resourceAssignments[{resourceName, startDate, endDate}]}]` | current memberships, retired resources included; `thresholdlimited` tests `first_threshold` only |
| `GET user/{u}/assignedresource?thresholdlimited=` | `[{resourceName, projectAssignments[{projcode, primary, title, startDate, endDate}]}]` | the same rows pivoted |
| `GET user/{u}/userrolelogin` | `["user", "role1", ...]` | the user, then active role accounts naming them as contact person |
| `GET user/{u}/wallclockexemption?active=` | `{username, resources[{resourceName, queues[{queueName, exemptions[...]}]}]}` | `username` echoes the path as sent |
| `GET search/projcode?fragment=&username=` | `[{projcode, title}]` | active projects, never 400; no fragment returns all |
| `GET project/{p}/hierarchy` | `{projcode, parentProjcode, rootProjcode, children[...]}` | the whole tree from the root, active children by projcode; project must be active |
| `GET report/project/{p}` | `{projcode, title, lead*, admin*, abstractText, hierarchical, accounts[...]}` | every account, every non-future allocation; inactive projects served |
| `GET report/usage/project/{p}` | `{projcode, reportDate, accountReports[...]}` | project must be active; rows `DataHoldings` (disk) then `AccruedCharges`, each by resource |
| `GET access` | `[{resourceName, resourceType, login, shells[{shellName, dflt}]}]` | configurable resources named like an access branch |
| `GET access/resource/{r}` | one element of the above | 404 when `{r}` exists but is no branch |

### The usage report

- **Status** follows legacy's `DefaultAccountStatusCalculator`: No Account > Waiting / Expired /
  No Allocation > Disabled > Normal for a charging-exempt project on a chargeable resource >
  the parent's non-Normal charging status > Overspent > Exceed Two / One Threshold > Normal. A
  child inherits its parent's No Account on a resource the parent lacks.
- **Charges** are the project's own subtree through the accounting kernel; `totalCharges` =
  charges + adjustments, `balance` = amount - that.
- **Thresholds** (30- and 90-day) only on `AccruedCharges` rows in a charge status:
  `allocationAmount = period x amount / (days(end) - days(start))`, legacy's day count (also
  fstree's and the rolling windows'); `percentLimit` only when both thresholds are set.
- **Holdings** are decimal TB (bytes / 10^12) and file counts at the resource's latest scan.

## Deliberate fixes (each a named parity rule)

| Rule | Legacy | SAMuel |
|---|---|---|
| F1 zero threshold allocation | 500 | `allocationAmount` and `percentUsage` null |
| F2 lowercase projcode on usage | 500 | 200, stored projcode |
| F3 order | HashMap/Set order | sorted (rows, `access`, shells) |
| F4 amounts | float32 sums | exact; differs by at most 1 |
| F5 null end date | 500 | `null` |
| F6 default project race | another user's rows possible | gone |
| F7 unknown user on wallclock | HTML 500 | the 400 envelope |
| F8 `%`/`_` in `fragment` | wildcards | literal |
| F9 `reportDate` | honored only on a cold cache | 400 `reportDate is not supported.` |

Also differs: legacy's usage replay ignores date-only ADJUSTMENT transactions, so after an end
date is edited it reports the original date; SAMuel reports the allocation row.

## Not ported

`PUT user/{u}/access`, `PUT user/{u}/defaultproject`, `PUT user/{u}/group`, and the reports
`hierarchicalusage`, `monthlycomputeusage[detail]`, `usercomputeusage[detail]`,
`allocationchange`, `dataholdings[detail]`: zero calls in three 30-day log windows. They
answer 404 or 405.

## Callers and parity

About 125 calls a day: the portal (Ruby, AWS) and a python-httpx client (`report/usage` only).
Compare a deployment with legacy:

```bash
SAM_HEUV_USER=... SAM_HEUV_PASS=... python utils/parity/check_legacy_apis.py --api heuv \
    --new-base-url https://samuel.k8s.ucar.edu --new-max-rpm 60
```

`--new-max-rpm` keeps the run under the shared rate-limit bucket. Cutover runbook:
`docs/plans/HEUV_API_PORT.md` §12.
