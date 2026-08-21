# XRAS write probes — the runbook and the results

**Status: run 2026-08-21 against production `api.xras.org`, operator-approved call by call.**
This is Phase 0 of [`../../plans/XRAS_REMEDIATIONS.md`](../../plans/XRAS_REMEDIATIONS.md): the
targeted probe that settles the ⚠️-marked half of the write surface
[`XRAS_WRITE_FIXUPS.md`](XRAS_WRITE_FIXUPS.md) § 2 left documented-but-untested. Everything below
was executed by hand with `curl`; nothing here has, or needs, a client behind it.

The point of writing it down is that **this API cannot be re-probed cheaply**. Half the calls are
destructive and the useful targets are a handful of NCAR test requests that must be left where
they were found. Read this before adding a verb to the write client.

---

## 1. Methodology

Four headers on every call. The status code is the finding:

| Code | Reading |
|---|---|
| **401** | the key is not provisioned for that route — a configuration fact, not an outage |
| **404** (JSON body) | the route **accepted us**; only the target failed to resolve. Capability is real |
| **404** (Tomcat HTML) | XRAS proxied us onward and the *downstream* service answered — see § 4.6 |
| **400** | accepted and validated; the body carries the reason |
| **200** | proves the call was allowed. It does **not** prove anything changed — § 4.4 |

```bash
source ../.env                       # XRAS_API_KEY et al.  Never echo it; never `curl -v`
BASE=https://api.xras.org
H=( -sS --max-time 25 -w '\n[HTTP %{http_code}]\n'
    -H "XA-API-KEY: $XRAS_API_KEY" -H "XA-ALLOCATIONS-PROCESS: NCAR" )
curl "${H[@]}" -H 'XA-CONTEXT: submit' -H 'XA-USER: msmart' \
     "$BASE/v1/requests/1167091/actions/30578/validate"
```

**Recording rule.** Status codes, state transitions, field names and shapes go in this file.
Response *bodies* do not — rosters carry researcher emails and this document is committed. NCAR
staff usernames and XRAS-side ids are fine; that is the bar `XRAS_WRITE_FIXUPS.md` already sets.

### Targets, and why these

| | |
|---|---|
| **NCAR0001** — requestId `1166819` | 2015 test request, `Approved`, one action `29980 New/Approved`. Roster: **`dhart` PI (13)**, `bjsmith` Allocation Manager (14) |
| **NCAR0007** — requestId `1167091` | 2015 test request, `Approved`, two actions: `30576 New/Approved` and `30578 Supplement/Incomplete` (left Incomplete by the 2026-08-20 withdraw demo) |
| `benkirk` | the CSG-controlled account for the role probes. **Confirmed to resolve in XRAS first** — an unknown username would mint a new identity |

⚠️ **None of these request numbers is a `project.projcode` in SAM** (checked). That is what made
them safe to write to. Every write below was paired with its inverse, and § 5 records that both
targets ended the session exactly as they began.

---

## 2. Results

| # | Probe | Verdict |
|---|---|---|
| P-A | `GET /v1/types/roles` | ✅ 200 under **both** `submit` and `report` |
| P-B | `GET /v1/reports/request_numbers/<n>` | ✅ 200 (`report` only) — the roster/action source |
| P0 | `GET /v1/people/<u>` under `submit` | ✅ 200; unknown username → clean 404 |
| P1 | `GET /v1/requests/<requestId>` | ❌ **401, route-wide** — `rules{}` is unreachable |
| P2 | `GET /v1/requests/<rid>/actions/<aid>/validate` | ✅ 200, **role-holder-scoped**, ⚠️ verdict varies by `XA-USER` |
| P3 | `POST /v1/requests/<rid>/actions/<aid>/submit` | ✅ 200 — re-submit authorized |
| P4 | `DELETE /v1/requests/<rid>/actions/<aid>/submit` | ✅ 200 — withdraw (re-confirmed) |
| P5/P6 | `POST\|DELETE /v1/roles/<requestNumber>/<roleTypeId>/<u>` | ⚠️ **provisioned but unusable here** — 404 "requestNumber not found" |
| P7 | `POST /v1/requests/<rid>/roles/<roleType>/<u>` + `DELETE /v1/requests/<rid>/roles/<roleId>` | ✅ **both** — this is the role family to build on |

---

## 3. What each probe settled

### P-A — the NCAR role type ids, confirmed live

```
13  PI                  displayRoleType "Project Lead"    relativeOrder 1
14  Allocation Manager  displayRoleType "Project Admin"   relativeOrder 2
19  User                displayRoleType "User"            relativeOrder 3
```

Three role types, all `isActive`. **No co-PI in the NCAR process** — the published apidoc example
is XSEDE's (1 CoPI / 2 PI / 3 Allocation Manager / 9 User) and does not apply to us. The ids are
process-scoped, so this had to be read live rather than trusted from an earlier session.

⚠️ **`displayRoleType` is XRAS's own operator vocabulary — "Project Lead" and "Project Admin", not
"PI" and "Allocation Manager".** The remediation UI should render `displayRoleType` and reserve the
raw `roleType` for the wire, so an operator reading SAM and the XRAS admin app side by side sees one
vocabulary.

`types/*` answers under `submit` **and** `report`, so a client in either context can resolve them.

### P0 — person reads work under the write context

`GET /v1/people/<u>` returns 200 under `XA-CONTEXT: submit`, with the same field set the read
client sees (`username, firstName, middleName, lastName, email, phone, organization,
academicStatus, residenceCountry, isReconciled, orcid, hasOrcidToken`), and a clean 404 for an
unknown username.

**Consequence:** `XrasAdminClient` verifies its own merges. It does not need to borrow the read
client, and the merge pre-capture (`residenceCountry` especially — merge does not copy it) happens
on the same connection as the write.

Also confirmed: the standing fixture `mding-user-efmlx` still resolves with `isReconciled: true`.
It was **not** merged — it is reserved for the end-to-end UI smoke.

### P1 — ❌ the legal-moves read is closed

`GET /v1/requests/<requestId>` returns **401 with an empty envelope** for every combination tried:

- contexts `submit`, `report`, `review`, `admin`
- users `msmart` (role-holder on 1167091), `bjsmith` and `dhart` (role-holders on 1166819), `arcguest`
- both requestIds

It is the **route**, not the target — the sub-resources beneath it (`/validate`, `/submit`,
`/roles`) all answer for the same key and the same users. So the documented
`rules{allowedOperations, allowedActions, existingActions[].allowedOperations}` block, which the
plan intended to use as the authoritative "what may I do to this action" read, **is not available
to us.**

**Consequence — a real design change.** `get_request()` comes out of the admin client. Offer
legality has to be derived from what we *can* see:

1. the sweep snapshot's action states (`actionStatus`), for which button to render, and
2. the **validate preflight** (P2), as the authoritative pre-flight before a re-submit.

A withdraw offer keys on `actionStatus not in ('Incomplete', ...)`; a re-submit offer keys on
`actionStatus == 'Incomplete'`. The modal's live read stays the authority, but that read is now
`reports/request_numbers/<n>` + `validate`, not `rules{}`.

### P2 — validate works, and ⚠️ its answer depends on who you impersonate

Role-holder scoped exactly like withdraw — `arcguest` is 401 on every request, `bjsmith` is 401 on
NCAR0007 (no role there) and 200 on NCAR0001 (Allocation Manager).

The finding that matters is not the scoping. It is this pair, same request, same action, same
second:

| `XA-USER` | NCAR0001 action 29980 |
|---|---|
| `dhart` (PI) | `{"validation":"successful","errors":[]}` |
| `bjsmith` (Allocation Manager) | `{"validation":"failed","errors":["The Project Lead specified for this request is not allowed to submit a new request in this opportunity"]}` |

**Validation is evaluated relative to the impersonated user, not the request.** So:

- A "failed" preflight is **not** terminal — it may only mean *this* impersonation cannot submit.
- The re-submit modal must **name the impersonated user next to the verdict**, and default to the
  request's **PI** (roleTypeId 13), not merely any role-holder.
- Caching a validate result across users would be wrong.

Second finding: NCAR0007's Supplement 30578 — a stale 2015 action — validates **successfully**.
Plan § 11.2 asked whether stale requests can ever pass validation. They can, at least here, so the
"withdraw is reversible" copy stands without qualification.

### P3 / P4 — re-submit is authorized, and the round trip is exactly net-zero

```
BEFORE   requestStatus=Approved | 30578:Supplement=Incomplete  30576:New=Approved
P3  POST   /v1/requests/1167091/actions/30578/submit   as msmart  -> 200
AFTER    requestStatus=Approved | 30578:Supplement=Under Review 30576:New=Approved
P4  DELETE /v1/requests/1167091/actions/30578/submit   as msmart  -> 200
AFTER    requestStatus=Approved | 30578:Supplement=Incomplete  30576:New=Approved
```

- **Re-submit lands in `Under Review`, not `Submitted`.** The status vocabulary the card renders
  must expect that.
- The request stayed `Approved` throughout, because its sibling New action is still approved —
  the mirror image of the § 2 withdraw finding, and the same reason "close this request" is really
  "act on each action".
- ⚠️ **The 200 carried `{"message":null,"result":null}`** — the apidoc advertises
  `result: [ <request> ]`. A caller that trusted the body to report new state would read `null` and
  have nothing. **The verify re-GET is not optional**, which is what the client design already says
  for a different reason (the `isReconciled` lesson). Two independent proofs now.

### P5 / P6 — the roles-by-projcode family is provisioned but cannot address our targets

`POST /v1/roles/NCAR0001/19/benkirk`:

| `XA-USER` | Result |
|---|---|
| `arcguest` | **401** — not a role-holder |
| `dhart` (PI) | **404** `{"message":"requestNumber 'NCAR0001' not found"}` |

401 for one user and 404 for another **on the same route** is the whole methodology in one place:
the key *is* provisioned for this family, and the scoping is the same role-holder rule as
everywhere else. It simply cannot resolve `NCAR0001` — nor `NCAR0007`. A **current** request number
(`NCAR4279`) does resolve; see § 4.6.

So this family is untested for writes and stays that way: testing it needs a **live** NCAR request,
which is a real project with a real roster, and that is not a probe — it is a production change.

### P7 — ✅ the requests-keyed role family works, and it is the one to build on

Two attempts, and the first one is the trap:

```
POST /v1/requests/1166819/roles/19/benkirk    as dhart  -> 400 {"message":"Invalid roleType '19'"}
POST /v1/requests/1166819/roles/User/benkirk  as dhart  -> 200 {"result":{"roleId":580030}}
DELETE /v1/requests/1166819/roles/580030      as dhart  -> 200
POST /v1/requests/1166819/roles/User/benkirk  as msmart -> 401   (not a role-holder)
```

Roster verified after each step; it ended as it began (`dhart` PI 13, `bjsmith` Alloc Mgr 14).

⚠️ **The two families encode `roleType` differently.** `/v1/roles/<requestNumber>/<roleType>/<u>`
documents it as *"Must be a Integer"* and takes `19`; `/v1/requests/<rid>/roles/<roleType>/<u>`
takes the **string** `User` and rejects `19` outright. Both spellings appear in the apidoc under
the same parameter name. Any client must therefore carry **both** representations of a role type,
and the 400 is the good outcome here — a family that silently accepted the wrong one would be far
worse.

Other findings:

- **No person params needed.** All of `firstName … isReconciled` are optional on this route, and
  omitting them entirely is accepted for a username that already exists. That defuses the reason
  the plan preferred the other family: the `isReconciled`-defaults-true create trap is avoided by
  *not sending the parameters*, not by choosing the route.
- **Add returns `{roleId}`; delete is keyed on that `roleId`**, not on the username. So
  `remove_role` needs a roleId, which comes from the reports roster (`roles[].roles[].roleId`) —
  already in hand wherever the sweep index is.
- Scoping is the familiar rule: `XA-USER` must hold a role on **that** request.

---

## 4. Cross-cutting facts worth carrying into the build

### 4.1 One authorization rule covers the whole write surface

Every request-scoped operation — validate, submit, withdraw, role add, role delete — authorizes on
**`XA-USER` holding a role on that request**, and 401s otherwise. `arcguest`, the config default,
is never sufficient. Person operations (merge, `/v1/people` reads) are user-agnostic.

That is a clean two-way split, and it is the shape the client should have: person ops take no
`xa_user`; request ops require one and should refuse to guess it.

### 4.2 Prefer the PI, and record who you impersonated

P2 shows the PI and the Allocation Manager are not interchangeable — the same call succeeds for one
and fails for the other. Resolve `roleTypeId == 13` from the roster and impersonate that; fall back
to another role-holder only deliberately, and put the choice in the audit row (`xa_user`) alongside
the operator who actually clicked (`created_by`).

### 4.3 Roster shape, from `reports/request_numbers/<n>`

```
roles: [ { person: {username, isReconciled, …},
           roles:  [ {roleId, role, roleTypeId, beginDate, endDate, isAccountToBeCreated} ] } ]
```

Note the **nesting** — one person entry carries a list of role entries. The reports payload has no
top-level `roleType` on the outer object; reading `role['roleType']` there returns `None`. The
sweep's index-entry builder must walk the inner list.

### 4.4 Never trust a 200 — now proven three ways

`POST /v1/people/<u>` with `isReconciled=false` returns 200 and ignores the parameter (2026-08-20);
`POST .../submit` returns 200 with a `null` result where the docs promise the request object; and
merge's 200 says nothing about whether the source is gone. Verify-after-write belongs in the
client, not in each caller.

### 4.5 The write surface, consolidated

| Op | Endpoint | Status |
|---|---|---|
| Merge person | `POST /v1/people/<u>/merge/<new>` | ✅ user-agnostic, destructive |
| Person read | `GET /v1/people/<u>` under `submit` | ✅ the verify path |
| Validate action | `GET /v1/requests/<rid>/actions/<aid>/validate` | ✅ role-scoped, **impersonation-dependent** |
| Submit action | `POST /v1/requests/<rid>/actions/<aid>/submit` | ✅ role-scoped; → `Under Review` |
| Withdraw action | `DELETE /v1/requests/<rid>/actions/<aid>/submit` | ✅ role-scoped; → `Incomplete` |
| Add role | `POST /v1/requests/<rid>/roles/<roleType string>/<u>` | ✅ role-scoped; returns `roleId` |
| Remove role | `DELETE /v1/requests/<rid>/roles/<roleId>` | ✅ role-scoped |
| Role types | `GET /v1/types/roles` | ✅ both contexts |
| Roles by projcode | `POST\|DELETE /v1/roles/<requestNumber>/<roleTypeId>/<u>` | ⚠️ provisioned, unverified — needs a live request to test |
| Request detail / `rules{}` | `GET /v1/requests/<rid>` | ❌ 401 route-wide, every context |
| Un-reconcile | `POST /v1/people/<u>` `isReconciled=false` | ❌ 200 and silently ignored |
| Delete request | `DELETE /v1/requests/<rid>` | ❌ 401 for every `XA-USER` |

### 4.6 The accounting-service loopback, and what cutover does to it

`GET /v1/roles/<requestNumber>/Users` is documented as *"retrieves users on resources from the
accounting service"*. Measured today:

| Request number | Result |
|---|---|
| `NCAR0001`, `NCAR0007` (2015 test) | JSON 404 — XRAS could not resolve the number, never proxied |
| `NCAR4279` (current) as its PI | **Tomcat HTML 404** — XRAS resolved it and proxied onward; the *downstream* service answered |
| `NCAR4279` as `arcguest` | 401 — scoping applies before the proxy |

The Tomcat page is the tell: for NCAR the accounting service is **SAM's legacy Java API**, and that
HTML 404 is its answer, not XRAS's.

⚠️ **After the ACCESS repoint next week that loopback routes back to us** — to SAM's own inbound
API. `GET /v1/roles/<n>/Users` then becomes a genuinely useful post-cutover diagnostic: a live
round-trip test of SAM's inbound API *as XRAS sees it*, exercising the path no SAM-side test can
reach. Today's Tomcat 404 is the recorded **pre-cutover signature** — re-run this exact call after
the repoint and a SAM-shaped response (or a SAM-shaped error) is the confirmation. `POST` to the
same family is a loop SAM must never drive.

---

## 5. Net-zero confirmation

Every write was paired with its inverse and verified by re-read. Final state, re-confirmed at the
end of the session:

| Target | Start | End |
|---|---|---|
| NCAR0007 action 30578 | `Incomplete` | `Incomplete` ✅ |
| NCAR0007 action 30576 | `Approved` | `Approved` ✅ |
| NCAR0007 request | `Approved` | `Approved` ✅ |
| NCAR0001 roster | `dhart` PI(13) 38569, `bjsmith` AllocMgr(14) 38570 | identical ✅ |
| `mding-user-efmlx` | resolves, `isReconciled: true` | untouched ✅ |
| `kquagraine-user-89o84` | not probed | untouched ✅ |

Role `580030` was created and deleted within the session and does not exist.

---

## 6. What this changes in the plan

| Plan § | Change |
|---|---|
| § 3 table | `rules{}` ❌ (was ⚠️); validate/submit ✅; roles → the **requests** family ✅, projcode family stays ⚠️ |
| § 5.1 | drop `get_request()`; `add_role(request_id, role_type_name, username, *, xa_user)`; `remove_role(request_id, role_id, *, xa_user)` |
| § 5.3 | role mapping must carry id **and** name; add a PI-resolution helper (roleTypeId 13) |
| § 5.4 | index entry needs `roles[].roles[].roleId` and the PI username; walk the nested roster |
| § 7.3 | Withdraw/Re-submit modals lose the `rules{}` read; the re-submit modal names the impersonated user beside the validate verdict |
| § 11.2 | closed — stale actions *can* validate successfully; the reversibility copy needs no qualifier |
| § 11.3 | closed differently — the CSG test account worked, but on the requests-keyed family |

---

## 7. References

| | |
|---|---|
| [`XRAS_WRITE_FIXUPS.md`](XRAS_WRITE_FIXUPS.md) | § 2 — the 2026-08-20 probe this extends |
| [`../../plans/XRAS_REMEDIATIONS.md`](../../plans/XRAS_REMEDIATIONS.md) | the feature these probes unblock |
| `https://api.xras.org/apidoc.html` | the published surface; static pages, plain `curl`, no key needed |
