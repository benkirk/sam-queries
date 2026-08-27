# XRAS Cutover Triage Week

**Status: ACTIVE.** Written 2026-08-24, the day of the #433 cutover — XRAS
repoints `sam.ucar.edu` → `sam.hpc.ucar.edu` and the incoming path becomes
live production traffic. This is the operating manual for the week: the
deploy loop, the watch, and the expected-failure inventory that separates
known failures from novel ones. Branch: `xras_incoming_triage` (this doc is
its first commit); the living PR vs `staging` tracks the week's work.

Prior art this doc leans on rather than repeats:
`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` (the cutover checklist),
`docs/xras/incoming/XRAS_TRIAGE_PLAYBOOK.md` (per-failure recipes), and
`docs/plans/XRAS_DATA_MODEL_UPLIFT.md` (what PR #479 changed and why). The
first two predate #479 — deltas are noted here, not rewritten there.

## Repoint ordering (the sequencing that matters)

1. ✅ PR #479 merged to staging (2026-08-24).
2. ✅ `xras_action_log` DDL applied to prod (`request_id` + index,
   `warnings` utf8mb4) — old code + new columns is safe; new code + old
   columns is not, which is why DDL preceded everything.
3. ⬜ Staging → main promotion PR.
4. ⬜ Prod deploy — the push to main auto-triggers
   `build-images-cirrus-deploy`; verify the new `sha-*` tag is what nwc1
   serves (`scripts/cirrus_healthcheck.sh` prints it).
5. ⬜ THEN the XRAS repoint. If XRAS repoints before step 4, the 11
   numberless-grant actions in the current cohort bounce as false 422s —
   the exact class #479 commit 1 removes.

## The living-PR loop (rapid deploys)

Work lands on `xras_incoming_triage`; the living PR vs staging stays open
all week. Deploying the branch to CIRRUS does NOT wait for a merge:

```bash
git push                                     # branch to origin
gh workflow run build-images-cirrus-deploy.yaml --ref xras_incoming_triage
gh run watch                                 # or gh run list --limit 1
scripts/cirrus_healthcheck.sh                # post-deploy gate
sam-admin cache --refresh                    # if cached fragments changed
```

What the dispatch does: builds the webapp image from the named ref, pins
`ghcr.io/.../webapp:sha-<short>` into `helm/values.yaml`, and force-pushes
the locked `cirrus` branch as the deploy App (a concurrency group
serializes racing deploys — a second dispatch queues, never races).

Rules of the loop:

- **Small batches to staging.** A fix that sits on the branch all day
  protects nobody once prod is back on a main-built image. Merge the living
  PR's contents into staging in reviewed chunks; after each merge, rebase
  `xras_incoming_triage` on staging and force-push (the PR follows).
- **While a branch-built image serves prod, prod runs code staging does not
  have.** The living PR body says which sha is deployed; update it when
  dispatching.
- **Never write a CI-skip token in prose** in commits, the PR title, or the
  PR body — GitHub scans the whole squash message, and this PR's body will
  eventually be one (the #406/#408 incidents). Break the string if the
  convention must be discussed.
- `gh pr checks` right after a push reporting nothing is the race, not a
  skip. Wait and re-ask.

## First-hour watch

- **Rows arriving:** `sam-admin xras --last 1h` (and `--summary`), or the
  Allocations → XRAS tab. Read-only against prod from a workstation:

  ```bash
  set -a; source .env; set +a
  export SAM_DB_USERNAME="$PROD_SAM_DB_USERNAME" \
         SAM_DB_SERVER="$PROD_SAM_DB_SERVER" \
         SAM_DB_PASSWORD="$PROD_SAM_DB_PASSWORD"
  sam-admin xras --last 1h
  ```

- **Live log follow** (webapp pods; every XRAS line is prefixed `XRAS `):

  ```bash
  kubectl logs -n sam-queries -l app=samuel -f --all-containers=true \
      --prefix | grep --line-buffered 'XRAS '
  ```

  The tasks pods are a DIFFERENT selector (`-l app=samuel-tasks`) — the
  hourly `xras_sweep` logs live there, not under `app=samuel`.

- **Log-access caveats** (from the `cirrus_healthcheck.sh` review): its
  section 10 greps only `ERROR|CRITICAL|Exception|Traceback`, and every
  XRAS operational line — "parked for a human", "completed with N
  warning(s)", "panels[] disagreement" — is WARNING/INFO level, invisible
  to it. The healthcheck is the health gate, not the XRAS watch. Pod logs
  are also ephemeral across rollouts; the durable record is the
  `xras_action_log` row, which since #479 carries `warnings` and
  `request_id` — so triage from the table, tail logs only for live color.

- **Health endpoint + edge:** the healthcheck script covers both; run it
  once after each deploy.

## Expected-failure inventory (measured 2026-08-24)

Sweep-replica preflight over the live cohort: **366 candidate actions in
the 120-day window → 324 would succeed, 15 would fail, 4 park, 23
incomplete**. A cutover failure NOT on this list is the interesting kind;
one that IS on it is pre-triaged, not news.

| Request | Action | Service | Expected failure |
|---|---|---|---|
| NCAR4279 | New#393444 | add | PI `cgriffin-user-fu8sr` not in database / username missing / no affiliation |
| NCAR4275 | New#393140 | add | Username `dlowry-user-spe13` missing |
| NCAR4261 | New#392319 | add | PI `ggeogdzhayev-user-7016v` not in database (its grant warning cleared in #479 and revealed this) |
| NCAR4262 | New#392007 | add | PI `glarouche-user-cj2nx` not in database |
| NCAR4252 | New#390940 | add | PI `sseyedzadeh-user-a85do` + AM `akhosronejad-user-sc52a` not in database |
| NCAR4212 | New#386948 | add | Contract `PRJ013992 BWI` unmatched (real number, leading zero — needs a contract row or review) |
| NCAR4231 | New#386569 | add | Contract `2423211` unmatched (same class) |
| UMMM0016 | New#383236 | update | Action end date before existing allocation end date (Casper GPU, Derecho GPU, …) |
| UNEB0017 | New#382870 | update | PI/AM `rdixon` inactive |
| UCOR0102 | New#382231 | update | Action end date before existing allocation end date (Campaign_Store) |
| UNOA0010 | New#379534 | update | Ambiguous Allocation Manager: `bwolding`, `hjimenez` |
| UJHB0034 | New#379039 | update | Ambiguous Allocation Manager: `jlundqui`, `yuan` |
| UCSU0136 | Ext#378879 | extend | End date before existing allocation end (2027-01-31) |
| UMCP0014 | Ext#384480 | extend | End date before existing allocation end (2027-09-30) |
| UCUB0160 | Adj#382993 | adjust | −500,000 Derecho GPU would take the allocation below zero |

Parks (4): Transfer for UCNN0063 (deliberately unserviced) + the unsampled
action types (`Advance`, `Date Adjustment`) if any arrive. The
missing-user rows are the Pending-Users worklist's job
(`sam-admin xras --accounts`); the two contract numbers need either a
`contract` row or a human verdict that they are not contracts.

## What #479 changed vs legacy (what an XRAS admin may notice)

- A `grants[]` entry with an empty or digit-free number **applies with a
  recorded warning** instead of bouncing 422 (`Cannot find contract for
  grant number "" ("")` is gone for that class; real-but-unmatched numbers
  still fail loudly).
- Two spellings of one contract (`2146709` / `AGS-2146709`) link once
  instead of raising a mid-transaction 500.
- Every audit row now records `request_id` (the request-line identity) and
  `warnings` (non-fatal facts: unlinkable grant, unflagged-primary fos
  fallback, roster disagreements) — visible in the action-details modal,
  `sam-admin xras --show N`, and JSON.
- Ended roles are excluded from the Pending-Users worklist and badged in
  the request modal; the worklist renders person detail from the payload
  with no XRAS lookups.
- `panels[]` can add a panel authorization the opportunityName ladder
  missed (never withdraws one); disagreements are logged.

## Tooling inventory

- `sam-admin xras` — `--last/--status/--type/--request` (list),
  `--show N --payload` (detail incl. warnings), `--recheck N` (would it
  succeed now; applies nothing), `--summary`, `--accounts [--enrich]`
  (Pending-Users worklist), `--readiness` (sweep snapshot board),
  `--mnemonic-report`, `--contract-report`, `--identity-report`, `--family PROJCODE`, `--person USERNAME`,
  `--validate-mapping`, `--validate-opportunities`,
  `--validate-vocabulary` (new: the hardcoded role/panel constants vs live
  XRAS + DB).
- The XRAS tab (Allocations dashboard): action log with filters, details
  modal (errors + warnings), Replay; the Remediations and Pending-Users
  cards.
- `scripts/cirrus_healthcheck.sh` — post-deploy gate (see log caveats
  above).
- Escalation: XRAS/ACCESS contact is hdt@ucar.edu (Travis Fair / Haris
  Brka); POSTs are human-triggered in xras_admin and never auto-retried,
  so a 422's body is read by a person and a re-post is a human decision.

## Cutover log

Times UTC. Prod DB reads are the workstation `PROD_SAM_DB_*` recipe above.

| When | What |
|---|---|
| 15:55 | staging `e15c40a7` (#479) dispatched to CIRRUS from the staging branch; prod carried #479 before the repoint, so ordering step 4 was met without waiting on the main promotion (#480). |
| 16:15:20 | XRAS base URL repointed to `https://sam.hpc.ucar.edu/api/xras/v1` and apps restarted (Steve Peckins, 11:15:20 CDT). |
| 16:21 | Unauthenticated probes from the workstation: `GET /people/benkirk` and `POST /actions` both answer the 41-byte 401 and land in the webapp pod log. Prod `xras_action_log`: 0 rows, no stranded `received`. Healthcheck 40 PASS / 3 WARN / 1 FAIL (the FAIL is section 12's task-dispatch envelope, not XRAS). |
| 16:25 | xras_admin "reconcile users" exercised; **no request reached the new host.** Either that screen reads XRAS's own cached roster (legacy pulled it by the 03:00 MDT cron) or XRAS is still talking to legacy — undecided until a legacy-side log check or a curl from XRAS. |
| 16:30 | First candidate post, UFSU0023 Extension #392184 (new end 2027-09-30), preflighted read-only before clicking: **would 422** — `end date is before existing allocation end date (2033-07-31)`. 2033 is the end of three `deleted=1` rows left by a renew-with-replace on 2026-08-04; the live rows end 2027-08-31. Novel class, not on the inventory: `latest_allocation()` did not skip soft-deleted rows. |
| 16:37 | Fix `be253d5d` (selector skips `deleted`; 459 XRAS tests green; prod preflight flips to `would_succeed`) pushed to `xras_incoming_triage`; branch dispatched to CIRRUS. |

| 16:46 | `sha-be253d5` rolled out (both pods); healthcheck unchanged from baseline. NCAR4277 New #390572 preflighted as the second sample: true `add`, would succeed. |
| 16:52 | UFSU0023 posted from xras_admin: **401**, the 41-byte legacy body ("Failed to initiate the action"). XRAS's request reached us (`Ruby` UA, username `XRAS`), so the repoint is real and the first "no traffic" hour was xras_admin reading its own cached roster. Row `XRAS` is enabled with `ROLE_XRAS`; the `samuel` credential passed the identical XA-header path. Cause: `bcrypt` 5.0.0 raises on a password over 72 bytes and `_bcrypt_matches` swallowed it as a wrong key, where Spring's encoder truncated silently. No `xras_action_log` row: the deny runs before `_record`. |
| 16:58 | Fix `bf4e37f1` (truncate to 72 bytes; log every API-key refusal with username and reason) dispatched. Meanwhile four XRAS person lookups (`qiangsun`, `mlevy`, `kkeene`) 401'd on the old image — real admins working. |
| 17:07 | `sha-bf4e37f` rolled out; healthcheck unchanged. |
| 17:09:41 | **First live action landed.** UFSU0023 Extension re-posted: 200 in 504 ms, `xras_action_log` #1 `processed`/`extend`, allocations 24992–24994 moved 2027-08-31 → 2027-09-30, three `EXTENSION` transactions with NULL amounts — the same shape as all 1,675 legacy XRAS extension rows. |
| 17:12:37 | **First New landed.** NCAR4277 → `UPSU0087` (GID 99058): 200 in 1.0 s, `xras_action_log` #2 `processed`/`add`, no warnings. Project row (lead/admin `dkpeng`, Small (No NSF award), UNIV USS), three `NEW` allocations (Derecho 500,000 · Casper 5,000 · Casper GPU 3,000, 2026-08-19 → 2027-08-31), roster `dkpeng` + `yingpan`. `active=0` by design — awaiting activate + notify on the XRAS card. |
| 17:21 | **First full lifecycle.** UPSU0087 activated from the XRAS card (`active` 0 → 1, `xras_activation_event` #1 `activated`) and notified (`notification_log` #1 `xras_activation` → lead, smtp, `queued` → `sent` in the same second, `xras_activation_event` #2 `notified`, `failed=0`). First production mail from `sam.notify`. |
| 17:34 | UFSU0023 notified from the card: `notification_log` #2 (lead) and #3 (admin), `xras_extension`, both `sent`, `xras_activation_event` #3 `notified` → action #1. `failed=0`. |
| 18:13:04 | **First organic approve → post.** UCNN0045 Extension #394352 (submitted 08-19, approved and posted today; allocations were a week from expiry): `xras_action_log` #3 `processed`/`extend`, 300 ms, 20590–20592 → 2027-08-31. Preflighted read-only at Requested and again at Approved before the post. |
| 18:14:50 | UCNN0045 notified: `notification_log` #4 (lead) and #5 (admin), both `sent`. Three actions, five messages, zero failures for the day. |
| 19:12–19:37 | Team demo: UMCP0037 #394505 and UFIT0017 #394646 extensions approved, posted (`xras_action_log` #4, #5), allocations → 2027-08-31. UMCP0037 notified (#6 lead, #7 admin). |
| 20:18 | `sha-e6084c9` rolled out: per-message `cc`/`bcc`/`sender`/`reply_to` on `Message`, read from `NOTIFY_XRAS_*` by `build_xras_messages` alone; values set to CC + Reply-To `alloc@ucar.edu` (team decision). |
| 20:19 | UFIT0017 notified (#8, lead=admin so one message): `cc=1 bcc=0` — the first copy in alloc@. Follow-up agreed: generalize to a per-family (`NotificationKind.family`) addressing convention applied in the `Notifier`, admin card iterating families, CronJob forwarding `NOTIFY_*` by prefix. |
| 20:39 | `sha-62d3b9c` rolled out: per-family addressing (`NotificationKind.family`, `NOTIFY_<FAMILY>_*` applied by the `Notifier`, CronJob forwards `NOTIFY_*` by prefix, admin card rows). Env in-pod unchanged; PR #481 body updated. |
| 22:39 | XRAS posted **NCAR4285** (New, action 394088) → **422**: Allocation Manager `sdahal` is inactive in SAM. Inventory class (inactive account), not code — the preflight had predicted it. Clears when the account is reactivated or XRAS drops the role, then XRAS re-posts. |
| 02:59 (25th) | Day-2 dispatch: `sha-1adf2dd` on both pods (PR #482 — healthcheck stderr fix, contract blockers Phase 1, pending-work queue, sort-header scroll fix). Healthcheck **41 PASS / 3 WARN / 0 FAIL**, section 12 PASS for the first time. The 03:00 sweep on the new image: `--contract-report` 3 targets (NCAR4231, NCAR4280, NCAR4212); pending work 47 of 467 swept. |
| 11:12 (25th) | `sha-06e63b8` rolled out: `XRAS_WRITE_ENABLED: "1"` (webapp only; drift test flipped in the same commit). In-pod parsed config `write_configured: True`; CronJobs carry no lever; healthcheck 41/3/0. |
| 11:16 (25th) | XRAS re-posted **NCAR4262** (New, action 392007) → **422**, `xras_action_log` #8: the three placeholder messages (PI `glarouche-user-cj2nx` not in database / username missing / no affiliation). As predicted; XRAS looked the placeholder up (404) 77 s before posting and posted anyway. |
| 11:18 (25th) | **First production merge from the card**: `glarouche-user-cj2nx` → `glarouche` (email-exact; SAM `glarouche` active). XRAS: placeholder 404, target retained; Pending Users row dropped; NCAR4262's index entry re-fetched with `glarouche` on the roster. Two defects surfaced: (1) **no `xras_remediation_event` row** — every write route passed `_session_factory()` (a Session) where the service takes the factory, so `_open_event` logged `'Session' object is not callable` and proceeded by design; fixed on #482 with a route-level gate. (2) The refreshed entry carries **no preflight** (`preflight_rollup: None`), so a just-fixed request drops out of the pending-work queue until the next sweep — follow-on: `_refresh_index_entry` should re-run the preflight. Merge copied no person detail (organization, phone, residence country), as documented. |
| 11:50 (25th) | `sha-8ed71ad` rolled out: write routes pass the session factory (audit rows), Feed A supersession + merged-away rules, preflight re-run on refresh. 0 errors. |
| 12:14 (25th) | XRAS re-posted **NCAR4262** → **422**, `xras_action_log` #9, one message, exactly as predicted: *Could not determine Mnemonic code for internal PI via organization*. The identity is fixed; the blocker moved to affiliation. Pending Users dropped the placeholder on the next render (#9 supersedes #8). Root cause: SAM's `glarouche` had a `user_institution` row for UNIVERSITY OF MIAMI (id 55, resolves to `MIA`) that the upstream affiliation sync **end-dated on 2026-06-24**; no organization row either, so the extractor fell to the organization route and reported the "internal PI" string. Fix is affiliation data, not code; see the day-3 notes. |
| 13:10 (25th) | `sha-fb99e21` rolled out: `is_pending_work` keeps a `seen_in_log` action whose latest log row is `received`/`failed`/`manual`; `log_seen_for` picks the highest log id. NCAR4262 back in the default queue at render time (48 of 465), no re-sweep. Diagnosis: the queue rule was calibrated on cutover day, when every log row was a success, so "seen in log" and "posted" were the same thing until the first failed re-post. |
| 13:45 (25th) | **Second merge, an *unreconciled* placeholder, via the service from a pod** (API validation before any UI): `jhu-user-fo5ee` → `jhu279` (NCAR4280; email-exact, same name, WVU/Wisconsin both on the SAM user). Verified 200; `xras_remediation_event` **#1** — the first audit row, `before_state` holding the WVU sheet; the refreshed entry carries a preflight (`refreshed_at` set, still `failed`: AM `skannenberg-user-uxqws` + contract `2624974`). Three findings that reshape the feature: (1) **XRAS resolves every active SAM account live** (`GET /v1/people` proxies SAM's identity service — six role-less SAM users all resolve, reconciled) so a merge target exists as soon as SAM has the user and **no create step is ever needed**; (2) `isReconciled` is not flipped by anything — the retained identity is already `true`, and the placeholder is deleted; (3) `search/people` matches name/username only, capped at 20, so a common surname ("Hu") hides the target while the full name ("Jie Hu") finds it. |
| 15:47 (25th) | `sha-11a7729` rolled out: the identity-merge feature (`docs/plans/XRAS_IDENTITY_MERGE.md`). Healthcheck 41/3/0. First render already found work the morning's probe could not: `ggeogdzhayev-user-7016v` reads **Ready to merge → `geogdzhayev`** (NCAR4261) — the SAM account was created at 14:37Z, after the sweep, and the row flipped at render with no sweep. `--identity-report`: 1 target, 6 need an account. |
| 16:58 (25th) | `sha-acd3d95` rolled out: Track B of `XRAS_DATA_MODEL_UPLIFT.md` (allocationDateType, the family seam, the affiliation message, identity badge, add-role copy). Healthcheck 42/2/0. A live re-check of NCAR4262 now reads **PI `glarouche` has no current institution or organization in SAM** — the affiliation class named as itself. |
| 18:59 (25th) | Third production merge, first from the **Ready to merge** flag: `ggeogdzhayev-user-7016v` -> `geogdzhayev` (NCAR4261). Audit row #2 `verified`, placeholder 404, roster PI moved with its roleId, index entry re-preflighted at click time: `PI geogdzhayev has no current institution or organization in SAM` — the affiliation class again, no sweep needed. The identity strip then showed a second target that surfaced on its own: `sseyedzadeh-user-a85do` -> `sseyedzadeh` (NCAR4252). NCAR4261 was never posted to SAM; XRAS admin must re-push after the affiliation fix. |
| 19:06 (25th) | Fourth merge, second from the flag: `sseyedzadeh-user-a85do` -> `sseyedzadeh` (NCAR4252). Audit row #3, placeholder 404, PI moved with roleId 573038, click-time preflight: `PI sseyedzadeh has no current institution or organization in SAM` — predicted from the Stony Brook `user_institution` row that arrived already end-dated. Identity strip empty; 4 need an account. **Three of the board's PIs (NCAR4261/4262/4252) now share the affiliation class** — the upstream sync, not identity, is the bottleneck. None of the three was ever posted; each needs an XRAS re-push after the fix. |
| 19:40 (25th) | Steve's 08-24 findings correlated by request id — XRAS's ids are our `rid`s, and this session's watch log kept SAM's side. The two slow lookups (`kbarragan` 3.5 s, `ncar_guest_11554795` 7.4 s on their clock) were **182 ms and 76 ms app-side**: the seconds sit between their client and gunicorn, on the edge path. The 60 s connect failure (`apauls`, 01:37:32–01:38:32Z) never reached the app and is the 08-17 connect-timeout class — pods stable 5 h, third independent client, unconfirmable from our account; handed to CIRRUS with the window. Steve's log clock is CDT. Key is 96 chars: question 1 closed. |
| 14:36–23:52 (25th) | **Fifteen organic posts, fifteen `processed`, zero failures** (`xras_action_log` #10–#24: 10 extensions, 4 supplements, 1 New) — the allocation team working the board on the new code. **#10 is NCAR4285's New → `UUSL0048`** (active, PI `hholmes`, 500,000 units 2026-08-24 → 2027-08-23): the first New project minted in production. XRAS dropped the inactive AM `sdahal` from the request before re-posting, so the inventory blocker cleared on their side, as the table predicted. `sdahal` is still inactive in SAM. Identity strip found a third self-surfaced target: `mrahnemoonfar-user-j3bc3` → `maryamr` (NCAR4231, a contract-report target too). Pods 0 errors; `/api/xras` 138×200 / 37×404 over 6 h. |
| 01:28–01:31 (26th) | **NCAR4231 unblocked end to end from the card, all three strips used in sequence.** Fifth merge, third from the flag: `mrahnemoonfar-user-j3bc3` -> `maryamr` (audit #4 `verified`; the XRAS merge call itself took ~14 s this time, 16.6 s end to end, flagged Slow — XRAS-side latency, the earlier four took ~2 s). Then the contract strip's seeded link created contract 2270 (`2423211`, `mode=manual`, title pre-filled from the wire), then Re-check: `rechecked`, **`would_succeed: true`**, no messages — mnemonic `LHI`, series `ULHI`, UNIV USS, Small. Contract report down to 2 targets (NCAR4280, NCAR4212); identity strip empty. Never posted: the next XRAS push should mint a `ULHI` project. |

| 06:30 (26th) | Day-4 open: prod `sha-e59c9af` (#482 + #484 promoted via #483), 41/3/0, first sweep on the consolidated resolvers identical to the previous image (1639/4088, 17 pending, 12 accounts), first authenticated XRAS lookup on the new image 200 in 165 ms; no action rows past #24. George answered the `user_institution` question — see below: not a feed defect, but 31/31 already-ended rows since 08-18 still have no successor, so the three PIs stay blocked on upstream data. |

| 14:59 (26th) | XRAS re-pushed **NCAR4252** (New, action 390940) → **422**, `xras_action_log` #25, one message: *PI sseyedzadeh has no current institution or organization in SAM* — the affiliation class, exactly as the click-time preflight predicted after the 08-25 merge. Identity merged, contract fine; blocked on the upstream collaboration (George). Strip still empty; contract report still 2. |

| 16:13 (26th) | XRAS posted **NCAR4275** (New, action 393140) → **200**, `xras_action_log` #26 → **`UCNN0065`** (GID 99060, lead `jschnaubelt`, Derecho 500,000 · Casper 5,000, 2026-08-19 → 2027-08-31, `active=0` awaiting the card). Inventory said it would fail on `dlowry-user-spe13`; the payload names only the PI — XRAS dropped the placeholder before posting, the `sdahal` move again. No warnings. Third New minted in prod; awaits activate + notify. |

| 17:01 (26th) | **`UCNN0065` activated outside the new stack.** `project.active` 0 → 1 at 11:01:42 MDT with no `xras_activation_event`, no `notification_log` row, no webapp write in either pod's log (collector POSTs only), and no other table touched in the window. Legacy SAM has no scheduled activation path (its `activateProject` callers are AMIE handlers), so the writer is almost certainly a person in the legacy admin UI. Consequence: the XRAS card sees an active project with no `activated`/`notified` event, so it stays in the attention queue as un-notified — and a Notify click would double-mail the PI if legacy already did. Process question for Ben: is the team still activating in legacy, and should the card treat `active=1` with no event as "activated elsewhere"? |

| 20:07 (26th) | Sweep: two newly Approved requests. **NCAR4282** — PI `ssharma-user-s8etm` (gmail placeholder, no SAM account; identity strip lists it; needs an account before a merge). **NCAR4281** — PI `kheyblom` has **two concurrent current `user_institution` rows**: Victoria (2021, still open, no mnemonic) and Michigan (since 08-10, resolves `MIC`); the first-current rule (ours and legacy's `findAny()`) lands on Victoria, so the mnemonic report offers "link University of Victoria". If Michigan is the real affiliation the right fix is upstream (end the Victoria collaboration — George's domain), not a Victoria link that would stamp the project with the wrong series. Ben's call. |

| 21:49 (26th) | URTG0040 Extension → `processed` (#27); UUTA0018 Extension → `processed` (#28). Routine. |
| 22:12 (26th) | **NCAR4261 landed — as an `update`, via a workaround the team ran themselves.** An admin minted `UNYU0028` in *our* dashboard (`POST /admin/htmx/project-create`, 16:11:45 MDT, NYU mnemonic chosen by hand — sidestepping the missing affiliation), renumbered the XRAS request to the projcode, and pushed 22 s later: action 392319 arrived as `requestNumber=UNYU0028`, `select_service` found the project and routed `update` (#29, 200, no warnings): three accounts, three `NEW` allocations (Casper 5,000 · Data_Access · CMIP AP, 08-18 → 2027-08-31), lead `geogdzhayev`. **Bug found:** the two `User` roles (`yizhang`, `zanna`) were silently dropped — step 4 guards on `project.accounts`, which was loaded empty during planning and not re-read after step 3 created the accounts. The admin added both by hand at 16:12:43/49. Fix on #485: expire the collection before the guard + regression test. No activation event (project created active); the card shows it un-notified. |

| 23:30 (26th) | **Approver comments: found and wired (Part 1).** The review team's note to the PI is not on the POST (29 payloads, 41 fixtures) but is `actions[].adminComments` on the reports feed — non-empty on 223/457 approved actions. New resolver `src/sam/integration/xras_api/comments.py` (projcode + `actionId`, fail-open), threaded through the Notify route and the `xras_notices` task into the notice context, and shown in the Request modal as "Approver's note". Mail templates deliberately untouched (silent drop) — Ben's template PR follows before anything is sent with it. Also: `sha-1b2f713` (update-handler membership fix) rolled out 22:5xZ, 0 errors. |

| 14:30 (27th) | **Dismiss reason made optional** (Ben's call): new `XrasDismissForm` (blank → NULL) for `xras_dismiss`; Comment keeps the required `XrasActivationEventForm`; modal marks the field "(optional)"; the card badge tooltip says "No reason given" instead of rendering `None`. |

| 15:13 (27th) | XRAS pushed **NCAR4281** (New, action 393550) → **422**, `xras_action_log` #30: *Could not determine Mnemonic code for external PI via institution* — the two-open-institutions case pre-triaged 08-26 (`kheyblom`: Victoria 2021 still open and unmapped, Michigan since 08-10 → `MIC`; first-current wins). Fix is upstream (end the Victoria collaboration) or a Victoria mnemonic link if that is really his affiliation — Ben's call, then XRAS re-pushes. Strip/contract reports unchanged. |

| 16:54 (27th) | NCAR4281 re-pushed three more times (#31–#33, identical 422) and NCAR4252 once (#34, affiliation, unchanged). **NCAR4282** (#35, action 393811) → 422 *PI sanjib has no current institution or organization*: XRAS reconciled the `ssharma-user-s8etm` placeholder onto the existing SAM user `sanjib` (since 2020; Howard row ended 2026-07-03), so it moved from the account class to the affiliation class. **George** (~11:00 MDT) says he fixed the end-date bug and is running a full reset — the tick now watches the 37-user cohort for open rows. Identity strip: **`skannenberg-user-uxqws` → `kannenberg` Ready to merge** (NCAR4280 AM; the account the admin was searching for this morning landed). `--accounts` 12 → 11. |

| 17:48 (27th) | **NCAR4231 landed: `ULHI0005`** (#36, action 386569, 200, no warnings) — lead `maryamr`, admin `zel`, contract 2270 (`2423211`) linked, six allocations 2026-07-28 → 2027-07-31 (Derecho 500,000 · Derecho GPU 2,000 · Casper 20,000 · Casper GPU 1,000 · Campaign_Store 10 · CMIP AP 1). The merge + seeded contract from 08-26 did their job. Activated 71 s later via **`POST /admin/htmx/project-update/ULHI0005`** (the Admin › Projects edit form), so no `xras_activation_event` and no notice — the team's activation path is the project edit page, which is what the "activated elsewhere" follow-up must cover. Identity strip: **two** targets, `cgriffin-user-fu8sr → cgriffin` (NCAR4279) and `skannenberg-user-uxqws → kannenberg` (NCAR4280). Pending push 16 → 15. Two `MismatchingStateError` tracebacks at 11:51 MDT were a replayed IdP callback with no preceding `/auth/oidc/login`; the same user logged in normally 15 s later — a candidate for the same one-WARNING treatment as `?error=`. |

### Questions for Steve (batched, not piecemeal)

1. ~~How many characters is the `XA-API-KEY`?~~ **Answered 08-25: 96** — the
   bcrypt truncation story is confirmed without anyone handling the secret.
2. Two `/people/{username}` lookups at 17:06:49Z (`ncar_guest_11554785`,
   `sortiz`) arrived with **no usable credential** (401 in 0.2 ms, no bcrypt
   run) while the lookups seconds earlier carried the XA pair. Is there a
   second code path in xras_admin that calls the accounting service without
   the XA headers?
3. Does xras_admin's "reconcile users" screen read the nightly `GET /people?`
   roster cache rather than calling SAM? (Explains the traffic-free first hour.)
4. "Recent submissions" drops a row once it is posted **and notified**, and
   the notified half is XRAS's own record. SAM now sends the handoff mail
   (`sam.notify`, the Notify button / `xras_notices`), so nothing tells XRAS.
   What clears it — an admin action in xras_admin, an API call SAM could make
   after a send, or a per-site setting? Until answered, posted rows sit in
   their queue and in ours (`docs/plans/XRAS_PENDING_WORK.md`).
5. When an identity lookup fails to *connect* (the 60 s `apauls` failure,
   01:37Z 08-25), what does xras_admin do — skip, retry, or post with a
   placeholder? And what are its connect/read timeouts and retry counts?

**George, asked 08-25, answered 08-26:** `creation_time` is only when the row
landed in SAM; `end_date` belongs to the upstream "collaboration" record, so a
row created after its own end is a historical record, not a defect, and
mnemonic logic must not read `creation_time`. (It does not: `_best_institution`
takes the first row whose start/end window contains now, the same rule as
legacy `User.getBestInstitution` → `findOptionalCurrentInstitution`.) That
retires the "feed is broken" reading and sharpens the real one, measured
08-26 06:30 MDT: of 45 `user_institution` rows created since 08-18, **31
arrived already ended — every one on a user created in SAM the same hour, a
median 2 h upstream lifetime (6 min–6 d), row created ~4 min after the end —
and 0 of the 31 has since received an open institution or organization row**,
nor a successor under a duplicate user (2 same-name rows, both ordinary
collisions). The other 14 (8 also new users, same institutions — Albany 12
ended vs 4 open) arrived open and are current. Follow-up for George (Ben sends): does
the source hold an open collaboration for these people (e.g. `geogdzhayev`,
user 31424: NYU 08-24 16:12:23 → 08-25 08:33:12, row created 08:37:29, nothing
else on the user) that the synchronizer is not matching — a re-key when the
identity is finalized would explain the 2-hour "pre-finalization" records — or
do they genuinely have no open collaboration, in which case NCAR4261/4262/4252
need an upstream re-affiliation before XRAS can post. Blocks the same three.

Inventory deltas: the other four date-conflict rows (UCSU0136, UMCP0014,
UMMM0016, UCOR0102) have no deleted rows and stand as genuine XRAS-vs-SAM
conflicts. Side note from the sweep detail: the accounts-needed sample lists
both `harrter` and `hartter`.

## Handoff — 2026-08-25 EOD (day 3)

Prod `sha-acd3d95`, public health green (23:53Z), CI green on #482 head
`4c95a92a` (mergeable; past the image: docs + the Needs-cell fix `686778b3`,
nothing prod needs). Morning: (1) errors + `/api/xras` status counts on both
pods, `--since=12h`; (2) `sam-admin --format json xras --last 12h` for rows
past #24; (3) `--identity-report` (strip was empty; 4 need an account);
(4) answers from Steve (Q2–5) and George; a George yes unblocks NCAR4261/
4262/4252, then XRAS re-pushes each; (5) `maryamr` merge click (NCAR4231).
NCAR4285 is DONE (`UUSL0048`). Ben's calls: squash #482 to staging;
the CIRRUS hand-off for the 01:37Z connect failure. When traffic is boring:
fold what is durable into the playbook and close the living PR.
