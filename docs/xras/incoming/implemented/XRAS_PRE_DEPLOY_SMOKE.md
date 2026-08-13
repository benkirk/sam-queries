# XRAS incoming → email — the pre-deploy smoke test on a local stack

> ☐ **In progress.** Branch `xras_incoming_smoke`, on top of `integration` at
> `dfaf7eb`. Findings and any code changes land here.
>
> **Round 1 (2026-08-09) is complete** — §§1–10 below, seven findings, four code
> changes. It ended by rebuilding the operator card, so **[`## Round 2`](#round-2--exercising-the-activity-ledger)
> is where a fresh session starts.** Read Round 1 for the reasoning; run Round 2.

**Operational checklist.** Everything below is a command to run, a page to click or
an observation to record. It is worked *through* — tick `☐ → ✅` in place and write
what you saw under [`## Findings`](#findings). The value of this document is the
record, not the recipe.

**Companions:** [`XRAS_CUTOVER_RUNBOOK.md`](../XRAS_CUTOVER_RUNBOOK.md) (what happens
after this passes) · [`NOTIFICATION_FRAMEWORK.md`](../../../plans/implemented/NOTIFICATION_FRAMEWORK.md)
(the mailer) · [`XRAS_REIMPLEMENTATION.md`](../XRAS_REIMPLEMENTATION.md) (the contract)

---

> ## ⚠️ The three facts that shape everything below
>
> | | |
> |---|---|
> | **The local DB is not obfuscated.** | `127.0.0.1:3306`/`sam` is a real production clone — 28,372 users, real names, real addresses. (`:3307` is the obfuscated test DB; that is the one that is always safe.) |
> | **`MAIL_SERVER` is the live relay.** | `.env` already sets `MAIL_SERVER=ndir.ucar.edu`, which advertises no AUTH and relays for all of `128.117.0.0/16` — it will deliver to any address on the internet. |
> | **`.env` is a symlink.** | `devel/.env → ../.env`. Anything set there reaches `webdev` (5050), `webapp` (7050) **and** the host conda CLI via `etc/config_env.sh`. There is no "just webdev" scope. |
>
> **Therefore `NOTIFY_REDIRECT_TO=benkirk@ucar.edu` is written in the same edit that
> sets `NOTIFY_ENABLED=1`, and is never unset for the duration of this exercise.**
> Every message is then re-addressed, logged `redirected` rather than `sent`, carries
> an `X-SAM-Original-To` header and has `REDIRECT_BANNER` prepended to the body. The
> banner is noise we accept; it is the price of the guarantee.
>
> **Do not run `sam-admin project --upcoming-expirations --notify` during this
> session.** With notifications armed it walks the real clone and attempts ~600
> sends — all landing on one inbox under the redirect, which is still ~600 emails.
> Expiration templates are out of scope for this run.

---

## Why this exists

PRs [#424](https://github.com/NCAR/sam-queries/pull/424) (XRAS reimplementation —
read API, action capture, all six handlers, the operator surface) and
[#428](https://github.com/NCAR/sam-queries/pull/428) (notification framework) have
both merged into `integration`. They have never been exercised **together**: #424
shipped an operator surface whose Notify button was a stub, and #428 shipped the
mailer that fills it in.

Everything downstream is expensive to change. The cutover is abrupt — ACCESS holds
one base URL and dual-posting is ruled out — and `zz-90`/`zz-91`/`zz-92` go to a DBA
on a single ticket. This is the last cheap opportunity to confirm the schema and the
operator workflow are right, and to settle the wording of the mail an NSF NCAR PI
will actually receive.

> **2026-08-11.** Two clauses above have moved, and neither changes the argument. The
> `zz-9*` DDL was applied to production on 2026-08-10 and the init scripts retired
> (`XRAS_CUTOVER_RUNBOOK.md` § gate 2), so that is no longer a pending ticket. And the
> cutover is *still* abrupt by choice: ACCESS offered a test instance of xras_admin and
> we **declined it** for this cutover (runbook gate 4). Dual-posting remains ruled out
> and is a separate question.

---

## Preconditions

| ☐ | Precondition | How to prove it |
|---|---|---|
| ✅ | On `xras_incoming_smoke`, both PRs present | `git log --oneline -2` → `dfaf7eb`, `24965d9` |
| ✅ | The three tables exist locally, and are empty | `SHOW TABLES LIKE 'xras%'` / `LIKE 'notification%'` → `xras_action_log`, `xras_activation_event`, `notification_log`, all at 0 rows — a clean slate |
| ✅ | Containers up | `docker compose ps` → `webdev`, `webapp`, `mysql`, `mysql-test`, `cache` healthy |
| ✅ | Resource key mapping seeded | `SELECT COUNT(*) FROM xras_resource_repository_key_resource` → 13. **Re-verified 2026-08-11 against the 41-payload corpus**, which uses **7** distinct keys — `144650` Casper (×23), `145575` Derecho (×21), `145576` Derecho GPU (×10), `145145` Data_Access (×8), `144552` CMIP Analysis Platform (×6), `146036` Casper GPU (×2), `144646` Campaign_Store (×1). All 7 resolve, so the ✅ still holds; the three beyond the original four were not previously named |
| ✅ | `benkirk` holds `MANAGE_XRAS` + `SYSTEM_ADMIN` | `USER_PERMISSION_OVERRIDES['benkirk'] = [p for p in Permission]` — `src/webapp/utils/rbac.py:304` |
| ✅ | XRAS write credential in the shell | `source etc/config_env.sh && echo $SAM_XRAS_USER` → `samuel`; `api_credentials` is empty locally, so step 3 must write the row |

---

## Four gotchas that shape the step order

Established by reading the tree before starting. Re-verify rather than trust — the
code moves.

**1 · The Notify button only exists while the project is inactive.**
`get_xras_pending_activation()` filters `~Project.is_active`
(`src/sam/queries/xras_activation.py:155`). Activating a project removes it from the
pending card — and the Notify button with it. So the run order is **Notify, then
Activate**, and a Supplement against an already-active project never surfaces on the
card at all. Both are things to *observe and record*, not work around. Note the
tension: the mail says "is now active", which argues activation should come first.

**2 · Re-notifying after a template edit is suppressed.**
`dedup_key = xras_activation:{projcode}:{action_id}:{address}`
(`src/webapp/dashboards/allocations/blueprint.py:1497`+) and `sent`/`redirected`
suppress a repeat. The operator UI has no `--force`. To iterate on wording:

```sql
DELETE FROM notification_log WHERE dedup_key LIKE 'xras_activation:UHSS%';
```

**3 · `benkirk` as PI resolves a mnemonic only via the *organization* strategy.**
`resolve_mnemonic_code()` (`src/sam/xras/extractors.py:466`) takes the **lab** route
whenever `opportunityName` starts with `'NCAR '` — and benkirk's lab (org 32,
"Computational & Information Systems Lab") has **no** mnemonic soft link: the
`mnemonic_code` description reads "Computational and Information Systems Laboratory"
and matching is exact-casefolded. He has no `user_institution` row either. His
current org 375, "High-End Services Section", *does* match — code `HSS`.

> **So the smoke payloads must use a non-`NCAR `-prefixed `opportunityName`.**
> Copying the proven triple from `new_ncar4253_ok.json` — `opportunityName:
> "Small Allocation (University)"`, `allocationType: "Small"`, panel
> `CISL Resource Support` — routes correctly.

Projcodes render as `<facility.code><mnemonic><NNNN>`, so expect **`UHSS0001`,
`UHSS0002`** (UNIV facility code `U`). `SHSS0001` already exists under CISL, so the
`U`-prefixed range is free — which also makes the synthetic rows trivial to find.

**4 · `API_KEYS_DB_TTL` is 60 s.** A 401 in the first minute after the
`api_credentials` row is written is the cache, not a bug. Wait and retry.

---

## The sequence

### 1 · Arm the two features ✅

Append a delimited block to `../.env` so teardown is a single delete:

```bash
# --- XRAS pre-deploy smoke (docs/xras/incoming/implemented/XRAS_PRE_DEPLOY_SMOKE.md) — remove after ---
NOTIFY_ENABLED=1
NOTIFY_TRANSPORT=smtp
NOTIFY_REDIRECT_TO=benkirk@ucar.edu
NOTIFY_QUEUED_STALE_SECONDS=300
XRAS_ACTIONS_CAPTURE_ONLY=0
XRAS_ACTIONS_ENABLED=all
# --- end XRAS pre-deploy smoke ---
```

`MAIL_SERVER` / `MAIL_PORT` / `MAIL_DEFAULT_FROM` are already present and stay as
they are — the redirect, not the relay, is the control. `XRAS_ACTIONS_CAPTURE_ONLY`
defaults to `'1'` (`src/webapp/config.py:62`); while it is on, every POST is recorded
and **nothing dispatches**.

- **Done when** the block is written and `NOTIFY_REDIRECT_TO` is non-empty. Check
  this *before* the restart — the restart is what arms it.

✅ Written at `../.env:127-136`, with a pre-edit copy of the file kept in the session
scratchpad. `NOTIFY_REDIRECT_TO=benkirk@ucar.edu` is set. Nothing is armed yet —
the running containers still hold the old environment until step 2.

### 2 · Restart and confirm the arming ✅

```bash
docker compose up -d --force-recreate webdev
docker compose exec -T webdev env | grep -E 'NOTIFY_|XRAS_ACTIONS_'
```

Then **Admin → Configuration → Notifications** (`/admin/htmx/configuration`) must show
enabled *and* the **"Redirecting to benkirk@ucar.edu"** warning line. That card is the
single visual confirmation that the guardrail is live.

- **Done when** the container env shows all six keys and the card names the redirect.

✅ All six keys present in `webdev`. Verified in-app rather than only in the shell:

| Probe | Result |
|---|---|
| `NotifyConfig.from_environment().summary()` | `enabled=True`, `transport=smtp`, `relay=ndir.ucar.edu:25`, `redirect_to=benkirk@ucar.edu` |
| `resolve_recipient('pi@example.edu')` | `('benkirk@ucar.edu', 'pi@example.edu')` — **the guardrail proven directly**: any address is rewritten, the original preserved |
| `get_webapp_config()` | `DevelopmentConfig`, `XRAS_ACTIONS_CAPTURE_ONLY=False`, `XRAS_ACTIONS_ENABLED='all'` → all 7 types parse |
| `registered_services()` | `add, adjust, extend, supplement, transfer, update` — all six handlers registered |
| `gather_runtime_state()['notifications']` | `enabled=True`, `redirect_to=benkirk@ucar.edu`, no `unavailable` flag → the card renders and `notification_log` is reachable |

### 3 · Credential + the real fixture corpus ✅

```bash
source etc/config_env.sh
python scripts/xras/seed_dev_actions.py --errors
```

Idempotently writes the `ROLE_XRAS` `api_credentials` row from
`$SAM_XRAS_USER`/`$SAM_XRAS_PASS` (guarded to local hosts — the obfuscated snapshot
ships the table empty, and config-based `API_KEYS_*` resolve to `roles=[]` and 403),
then posts **every** scrubbed production payload plus a deliberate 400 and 422.
`seed_dev_actions.py` globs the fixture directory (`sorted(source.glob('*.json'))`), so
this grew from 8 to **41** with the 2026-08-11 corpus and will grow again on the next
harvest — do not read the counts below as fixed.

Some `New` actions will fail. That is the measured ~30 % success rate, and it is what
makes the queue realistic enough to judge the operator surface.

- **Done when** `/allocations/xras` renders a mixed queue and
  `SELECT status, COUNT(*) FROM xras_action_log GROUP BY status;` shows more than one
  status.

✅ **Re-run 2026-08-11 against the 41-payload corpus: 43 rows — 18 `processed`,
20 `failed`, 4 `manual`, 1 `failed`/400.** By action type:

| `action_type` | status | http | n |
|---|---|---|---|
| Extension | `processed` / `failed` | 200 / 422 | 8 / 1 |
| Supplement | `processed` | 200 | 9 |
| Adjustment | `processed` / `failed` | 200 / 422 | 1 / 2 |
| New | `failed` | 422 | 17 |
| **`Date Adjustment`** | **`manual`** | **200** | **4** |
| *(malformed body)* | `failed` | 400 | 1 |

All four `Date Adjustment` rows carry `service = NULL` and
`outcome_reason = "no service matches actionType='Date Adjustment'"` — the parking path,
visible and explained, which is the thing this step exists to confirm.

The 17 `New` failures are the expected roster misses, not a regression: corpus usernames
are scrubbed independently of the obfuscated snapshot and resolve to no rows.

<details><summary>Original Round 1 result (8 payloads) — kept for provenance</summary>

Credential created and `ROLE_XRAS` linked; 10 rows written — **4 `processed`,
6 `failed`**, exactly the expected shape:

| Payload | HTTP | service | Why |
|---|---|---|---|
| `adjustment_uwis0064_manual.json` | 200 | `adjust` | processed |
| `extension_ucub0166_ok.json` | 200 | `extend` | processed |
| `supplement_ubrn0027_ok.json` / `_ucub0182_ok.json` | 200 | `supplement` | processed |
| `extension_ufsu0023_failed.json` | 422 | `extend` | *Action end date is before existing allocation end date (2033-07-31)* — fails as named |
| `new_ncar4232_failed.json`, `new_ncar4253_ok.json` | 422 | `add` | roster: PI/AM *is not in database* |
| `new_uwis0071_existing_ok.json` | 422 | `update` | same; note it correctly routed to `update`, not `add` |
| malformed body / bad `awardPeriod` | 400 / 422 | — | error paths as designed |

</details>

### 4 · Build the benkirk-lead payloads ✅

Written to a scratchpad directory, **not** `tests/fixtures/` — these are synthetic,
not corpus. Each starts as a copy of
`tests/fixtures/xras/actions/new_ncar4253_ok.json`:

| Payload | `requestNumber` | `grants[]` | Purpose |
|---|---|---|---|
| `smoke_a_new_with_contract.json` | `NCAR9001` | NSF grant block kept | New **with** contract; notified, then activated |
| `smoke_b_new_no_contract.json` | `NCAR9002` | `[]` | New **without** contract; stays inactive to receive the supplement |
| `smoke_c_supplement.json` | *(B's minted projcode)* | n/a | `actionType: "Supplement"`, 2 resources |

In all of them `roles[]` collapses to a single `PI`: `username: "benkirk"`,
`person.email: "benkirk@ucar.edu"`, `isReconciled: true`,
`isAccountToBeCreated: false`. Leave `opportunityName`, `allocationType`, `panels`
and `fos` **exactly** as the source fixture has them (gotcha 3), and keep
`actionId` / `requestId` distinct from the corpus so the audit rows are unambiguous.
Post with the same `XA-REQUESTER` / `XA-API-KEY` headers `seed_dev_actions.py` uses.

⚠️ On a Supplement, `awardedAmount` is the **increment, not the new total**
(see `supplement_ucub0182_ok.json`).

- **Done when** A and B each return 200 and

  ```sql
  SELECT request_number, status, service, projcode_result
  FROM xras_action_log WHERE request_number IN ('NCAR9001','NCAR9002');
  ```

  shows `status='processed'`, `service='add'`, and two minted `UHSS####` codes.

✅ Both processed. The gotcha-3 prediction held exactly — mnemonic `HSS`, facility
`U`, so:

| Payload | `requestNumber` | → projcode | Contract | Allocations |
|---|---|---|---|---|
| Smoke **B** (no contract) | `NCAR9002` | **`UHSS0001`** (project 5896, gid 99030) | none | Derecho 500,000 · Casper 5,000 |
| Smoke **A** (with contract) | `NCAR9001` | **`UHSS0002`** (project 5897, gid 99031) | `AGS-2524858` | Derecho 1,000,000 · Derecho GPU 2,500 · Casper 10,000 · Data_Access 1 |

Both arrived `active = 0` as designed, both with `benkirk` as PI and sole recipient.
Note the codes are **reversed** relative to the plan's A/B labelling — A failed on its
first attempt (Finding 3) so B drew the lower number.

### 5 · The queue and the pending card ✅

Drive `/allocations/xras`: the log table and its filters, the detail modal
(`xras_action_details/<id>`), and the pending-activation card — which should now list
both `UHSS####` projects with benkirk's address in the recipients column.

- **Done when** both synthetic projects appear on the card with a recipient email
  shown and no "none on file" warning.

✅ `get_xras_pending_activation()` returns exactly 2 rows — `UHSS0002` and `UHSS0001`,
both `New`/`processed`, neither notified nor dismissed — and
`get_xras_pending_recipients()` resolves one recipient each:
`{'name': 'Benjamin Shelton Kirk', 'email': 'benkirk@ucar.edu', 'role': 'lead'}`.

The four *processed* corpus actions produced **no** card rows, because all five
projects they touched are already active. That is gotcha 1's corollary showing up on
its own, before we did anything to provoke it — see Finding 5.

### 6 · Notify (before Activate) → the actual email

On project **A**: click Notify. The modal renders the real body for the first
recipient via `Notifier.preview()` — no ledger row, so previewing is free. Read it.
Then Send.

```sql
SELECT kind,status,transport,recipient,intended_recipient,subject,dedup_key
FROM notification_log ORDER BY notification_log_id DESC;
SELECT * FROM xras_activation_event ORDER BY xras_activation_event_id DESC;
```

- **Done when** the row names `benkirk@ucar.edu`, an `event_type='notified'` row
  exists, and the mail is in the inbox.

✅ Sent for both projects. ⚠️ **The rows read `status='sent'` with
`intended_recipient = NULL`, not `redirected`** — and that is correct, not a leak.
`NotifyConfig.resolve_recipient` (`src/sam/notify/config.py:148`) only rewrites when
`address != redirect_to`, so redirecting to the address we were going to mail anyway
is a no-op: no `intended_recipient`, no `redirected` status, and **no redirect
banner** in the body. The guardrail is unchanged for every other address — a probe
with `pi@example.edu` still returns `('benkirk@ucar.edu', 'pi@example.edu')`. Worth
knowing before reading a production ledger: `sent` does not prove redirection was
off.

### 7 · Activate A

Click Activate (`xras_activate/<project_id>`, behind an `hx-confirm`). Confirm the
project goes active, an `event_type='activated'` row appears, and — the observation
that matters — **A drops off the pending card**.

- **Done when** `Project.is_active` is true for A and the card lists only B.

### 8 · Supplement, and the second email

Post `smoke_c_supplement.json` against **B**'s projcode (still inactive). B's card row
should now carry the newer action and, having been notified earlier, an **"Out of
date"** badge. Notify again — the new `action_id` mints a new `dedup_key`, so this one
is *not* suppressed — and check the resource amounts in the mail reflect the
supplement.

Then post a second supplement against **A** (now active) and confirm it lands in
`xras_action_log` as `processed` but does **not** surface on the pending card. Record
that under Findings.

- **Done when** a second `redirected` row exists for B with a distinct `dedup_key`,
  and A's supplement is in the log but absent from the card.

### 9 · Template review and iteration

Edit `src/sam/notify/templates/xras_activation.txt` and `.html`. The `--watch` sync
picks up changes on *write*; if the watcher started late, touch the files. Re-open the
Notify modal to re-render the preview. To re-send after an edit, clear the dedup row
first (gotcha 2).

Judge deliberately:

- the subject line — `NSF NCAR Project {projcode} is now active`
- the resource / amount / units / end-date list, including a `Data_Access` row whose
  amount is `1.0`
- what a **no-contract** project looks like beside one carrying a grant
- the HTML as a real mail client renders it, not as source
- anything implying a facility — `xras_activation` is **not** facility-aware
  (`src/sam/notify/kinds.py`), so there is only one variant and it cannot branch

- **Done when** the wording is one we would send to an external PI, and `.txt` and
  `.html` agree.

### 10 · The admin notification surface

**Admin → Configuration → Notifications** (counts + redirect warning, `VIEW_SYSTEM_CONFIG`)
→ **`Details »`** (`/admin/htmx/notifications`, `SYSTEM_ADMIN`): the log page, its facet
chips, the row-detail modal, and the `suppressed` badge tooltip on any suppressed
attempt.

- **Done when** every row generated above is visible and correctly faceted.

### 11 · Teardown

Delete the delimited block from `../.env`, then:

```bash
docker compose up -d --force-recreate webdev webapp
docker compose exec -T webdev env | grep -E 'NOTIFY_|XRAS_ACTIONS_' || echo clean
```

**Data stays** (decided): the synthetic `UHSS####` projects, the `xras_action_log` /
`xras_activation_event` / `notification_log` rows and the `ROLE_XRAS`
`api_credentials` row remain in the local clone as reference. A snapshot refresh
clears all of it.

⚠️ The `project_code` counter and the allocated Unix GIDs are **consumed** — those are
the only side effects a row delete would not undo.

- **Done when** `NOTIFY_ENABLED` is absent from both containers, so the mailer is
  fail-closed again.

---

## Findings

*Recorded as the run proceeds. Each entry: what was observed, whether it is a defect
or a design question, and what (if anything) changed.*

**☐ 1 · The local `From:` address diverges from production.** Noted while arming
step 1, before any mail was sent. `../.env` sets `MAIL_DEFAULT_FROM=nusd@ucar.edu`
and `MAIL_USE_TLS=false`; `helm/values.yaml:246,248` sets
`MAIL_DEFAULT_FROM=sam-admin@ucar.edu` and `MAIL_USE_TLS=true`. So the mail reviewed
in this exercise will arrive **from `nusd@ucar.edu`**, not from the address
production will use.

That matters for template review — the From line is part of what a PI reads — and it
is arguably the more interesting question of the two: allocation mail plausibly
*should* come from NUSD rather than a generic `sam-admin`. **Decide which one
production should send as**, and if the answer is `nusd@`, `helm/values.yaml` needs
the change. The TLS difference is inert (ndir advertises no STARTTLS, and
`SmtpTransport` only upgrades when the server offers it).

**✅ 2 · Allocation amounts in the mail are rendered *compact* — reviewed and
accepted; do not re-propose.** Ben's call, 2026-08-09: `1.00M hours` is fine as it
stands. Recorded in full below because the reasoning against it is not obviously
wrong and someone will raise it again.

```
  - Casper: 10,000 hours through 2027-08-31
  - Data_Access: 1 through 2027-08-31
  - Derecho: 1.00M hours through 2027-08-31
  - Derecho GPU: 2,500 hours through 2027-08-31
```

`fmt_number`'s house rule — exact with commas up to 100,000, compact above — is
correct for a dashboard where space is scarce and the reader can hover. It is the
wrong rule for an **official allocation notice**: `1.00M hours` is the one number in
the message the PI will quote back, reconcile against their award, and plan on, and
we have rounded it. `500K` on `UHSS0001` has the same problem.

The argument made at the time was to render with `fmt_number(raw=True)` so amounts
always read `1,000,000`. **Rejected** — the compact form is consistent with every
other surface a PI sees, and the exact figure lives on the project page they are
being sent to. No change.

**☐ 3 · The `New` handler *links* an existing contract; it does not create one.**
Smoke A first went out with an invented `grants[].grantNumber` of `SMOKE-9001` and
was rejected 422 with `Cannot find contract for grant number "SMOKE-9001"`. Re-posting
with a real award number (`AGS-2524858`, already in `contract`) processed and linked
correctly via `project_contract`.

Not a defect — but it is a **cutover expectation worth stating in the runbook**: any
XRAS `New` action carrying a grant SAM has never seen will fail with a message about
the *contract*, not the grant, and the operator's remedy is to create the contract
first. Given `contract` holds 2,226 rows against a much larger NSF award space, this
is a plausible recurring `failed` reason in triage week.

**☐ 4 · Nothing in the mail distinguishes a project with a contract from one
without.** A and B differ only in their allocation lists; the linked award
`AGS-2524858` appears nowhere in the body. That may well be right — the PI knows
their own grant — but it was the explicit reason for building two payloads, so
record the decision rather than leave it implicit.

**☐ 5 · The processed corpus actions produced no card rows at all**, because
every project they touched (`UWIS0064`, `UCUB0166`, `UBRN0027`, `UCUB0182` in Round 1;
18 projects in the 2026-08-11 re-run) is already active. So Extensions, Adjustments and
Supplements against live projects were applied and left **no operator-visible trace on
the worklist**. The larger corpus makes the point 4½× harder rather than changing it.
This is
gotcha 1's corollary arriving unprompted, and it sharpens the design question: the
pending card is an *activation* worklist, not an *action* worklist, and there is
currently no surface that says "something changed on a project that is already
running". Whether that matters is Ben's call; the XRAS log page does record it.

**☐ 6 · No `New` action in the committed corpus can succeed on any local database.**
All 16 (was 3, before the 2026-08-11 corpus) fail on the roster
(`PI user_000000NN is not in database`) because
`scrub_payload.py` replaces usernames with pseudonyms, and
`SELECT COUNT(*) FROM users WHERE username LIKE 'user\_000000%'` is **0** on the
production clone (and the obfuscated snapshot uses a different `user_<hex>` shape).
Handler *logic* is unit-tested with factories, but the end-to-end
route → dispatch → handler → DB path for `add` had never run locally until this
exercise. Worth a line in the corpus docs so the next person does not read `_ok` as
"will succeed here".

**☐ 7 · Minor template nits**, all in `xras_activation.txt`/`.html`:
- a blank line between every allocation bullet (loop whitespace) makes a 4-resource
  list twice as long as it needs to be
- `Data_Access: 1` — the raw resource name carries an underscore, and a bare `1` has
  no unit, so the line reads like a truncation
- the title is interpolated into a pre-wrapped paragraph, so
  `Your project UHSS0002 - "Smoke Test A …" is now active on` already runs past 80
  columns with a short synthetic title; a real one will look ragged
- ☑ *"This message replaces the activation notice previously sent by the NSF NCAR
  allocations office"* was transitional wording — right for cutover, and the
  question raised here was whether it should age out. **Decided: dropped**
  (Round 2). It explains a changeover to recipients who mostly will not
  remember the previous arrangement, and it dates the mail from the day it
  ships. The activation notice now opens straight into what the reader needs:
  the project is active, and here are its allocations.

### Carried in as hypotheses

- **Notify is unreachable once a project is activated** (gotcha 1). If the intended
  operator order really is activate-then-notify, the card needs to retain
  recently-activated projects for a window, or Notify needs a second home on the
  project page.
- ~~**No force on the operator Notify** (gotcha 2).~~ **Confirmed in use, and
  fixed.** "Notify again" opened the modal, Send reported nothing delivered, and the
  ledger holds two `suppressed` rows (ids 2 and 4) from exactly that. The modal now
  offers a **Send again anyway** checkbox — but *only when a duplicate would actually
  be suppressed*: `xras_notify_form` asks `ledger.already_sent()` per recipient ahead
  of the click, so the toggle is never permanent furniture an operator learns to tick
  without reading. It overrides the dedup check **alone** — `NOTIFY_ENABLED` still
  fails closed — and a forced send is stamped on the activation event (*"Re-sent with
  the duplicate check overridden."*) so the timeline can explain why someone was told
  twice. Gotcha 2's `DELETE` recipe is no longer the only recovery.
- **A supplement to an active project is invisible** on the operator surface. May be
  correct by design; if not, it is a gap in the worklist.

## Template changes

*What changed in `src/sam/notify/templates/xras_activation.{txt,html}` and why.*

**✅ 1 · Point the portal link and its prose at SAM, not ARC.** Ben's call after
reading the delivered mail. Both files:

| | Was | Now |
|---|---|---|
| link | `https://arc.ucar.edu/` | `https://sam.hpc.ucar.edu/` |
| prose | "through the ARC portal" | "through the **SAM** portal" |

The mail sends a PI to manage members and request supplements — that is SAM's
surface, and naming ARC sends them to the wrong place. Verified the target is live
and its certificate validates (`302` to login, `ssl_verify=0`), so the link is
already good; it does not wait on the CNAME work in `project_cname_sam_hpc`.

Re-rendered both variants afterwards — text and HTML agree.

**✅ 2 · Stop asking the recipient to reply.** Ben's call after reading the delivered
mail: the `From:` address will eventually be a **no-reply** mailbox, so
*"please reply to let us know"* is an instruction the mail will not be able to
honour. Both files now read *"please contact help@ucar.edu"* (a `mailto:` link in
the HTML).

This is worth holding next to Finding 1: the local run sends as `nusd@ucar.edu` and
`helm/values.yaml:248` sends as `sam-admin@ucar.edu`, and **neither is a no-reply
address today**. The template no longer depends on that, which is the point — but the
`From:` decision is still open.

The expiration templates never carried this line, so nothing to sync there.

**✅ 3 · The Activate confirmation was styled as a destructive action.** Observed by
Ben immediately after activating `UHSS0002`: a bright red header and red Confirm
button, with the message *"Activate UHSS0002? This sets the project active and
records who did it."* — which reads as a scolding for what is the happy-path
completion of the workflow.

Cause: `htmx-config.js:268` intercepts `htmx:confirm` and opens the shared Bootstrap
modal, defaulting to `variant: 'danger'`. The button never overrode it. The
established convention is that constructive actions *do* override — `Grant resource
access`, `Make admin` and `Impersonate` all pass `data-confirm-variant="warning"`
plus a title and label; the red default is reserved for genuine destruction.

Changed in `partials/xras_pending_card.html` to `data-confirm-variant="info"` (blue
header, `btn-primary`), matching the green outline button, with
`data-confirm-title="Activate project"` / `data-confirm-label="Activate"`, and the
message reworded to *"Activate {projcode}? Its allocations become usable right away,
and the activation is added to the project's history."* — the audit fact restated as
history you can look at rather than a record kept against you.

This is the first use of the `info` variant; `warning` was the alternative, but
Activate is strictly more benign than "Make admin", and the modal has supported
`info` since it was written. No test asserts on the confirm text and the route map is
untouched.

⚠️ **This makes the two template families diverge.** `expiration-UNIV.txt:33` and
`expiration-UNIV.html:65` still say `https://arc.ucar.edu/`. Expiration notices are
out of scope for this run by decision, so they are deliberately left alone — but they
should get the same treatment before the next expiration send, or a PI will be
directed to two different portals by two different SAM emails.

## Round 2 — exercising the activity ledger

**Written for a cold start.** Round 1 ended by replacing the surface it was
testing, so nothing below assumes the session that produced it.

### What changed, and why Round 2 exists

Round 1's findings 5 and the two struck hypotheses were all one bug wearing three
hats: the card was keyed on the **project** and filtered on `~Project.is_active`.
So activating erased the Notify button, a Supplement against a live project was
invisible, and a second action needed a "stale" flag because one row cannot
represent two things happening.

Rows are now one per **processed action**, over a selectable window
(`d810bfc`). Also landed: four notification kinds instead of one
(`xras_activation` / `xras_supplement` / `xras_extension` / `xras_update`, with
`adjust` and `transfer` deliberately having none), an action-aware Notify
(`?action_id=`), a force override on the modal (`f3c7f0d`), and the mail now
points at the SAM portal and `help@ucar.edu` (`ed5fbc3`, `3284c23`).

**None of that has been exercised by a human.** Round 2 is that.

### Where things stand

| | |
|---|---|
| Branch | `xras_incoming_smoke`; suite **5,621 passed** + 21 stress, route map unregenerated |
| `.env` | **still armed** from Round 1 — `NOTIFY_ENABLED=1`, `NOTIFY_TRANSPORT=smtp`, `NOTIFY_REDIRECT_TO=benkirk@ucar.edu`, `XRAS_ACTIONS_CAPTURE_ONLY=0`. Re-confirm before posting anything (§2). |
| Local DB | 15 `xras_action_log` rows, 6 `notification_log` rows, projects `UHSS0001` / `UHSS0002` both **active**, `api_credentials` has the `ROLE_XRAS` row |
| ⚠️ Test hermeticity | `TestingConfig` now pins `XRAS_ACTIONS_CAPTURE_ONLY`, so a `.env` with `=0` no longer breaks ten API tests. If you see capture tests failing, that pin is the first thing to check. |

### The generator

`scripts/xras/smoke_payloads.py` replaces Round 1's hand-built scratchpad JSON,
because the interesting payloads name a projcode that does not exist until the
`New` before them has been processed. Print by default, `--post` to send:

```bash
source etc/config_env.sh
python scripts/xras/smoke_payloads.py --new --contract AGS-2524858 --post
python scripts/xras/smoke_payloads.py --new --post                  # no contract
python scripts/xras/smoke_payloads.py --supplement UHSS000N --post
python scripts/xras/smoke_payloads.py --extension  UHSS000N --post
python scripts/xras/smoke_payloads.py --renewal    UHSS000N --post  # → xras_update
```

Validated end to end: an `--extension UHSS0002` posted 200, dispatched to
`extend`, and moved all four allocations to 2027-12-23 (log row 15).

Two constraints the script encodes, both learned the hard way in Round 1 —
gotcha 3 for the first, finding 3 for the second:

- the opportunity must **not** start with `"NCAR "`, or mnemonic resolution takes
  the lab route and fails for any NCAR-lab lead;
- `--contract` must name a row that **already exists** in `contract`; the `New`
  handler links, it does not create.

### The sequence

Each step says what to look at, because looking is the point.

**R2.1 · Re-arm and confirm.** Repeat §§1–2. The Notifications card must name
the redirect target before anything is posted.

**R2.2 · Two new projects, with and without a contract.** Post both `--new`
forms. Expect `UHSS0003` / `UHSS0004`, both `active = 0`.
- **Look at:** both appear on the card tagged **Needs activation** and **Not
  notified**; the `Activation` chip now counts 2.

**R2.3 · Notify, then Activate — then Activate a project you have NOT notified.**
The Round 1 order was forced; it no longer is. Do one project each way.
- **Look at:** the activated project **stays on the table** with its badge
  flipped to Active, and its Notify button still works. That is the whole fix.

**R2.4 · Supplement, Extension, Renewal.** One of each against the new projects.
- **Look at:** each is a **new row**, not a mutation of an existing one; each is
  independently notifiable; the mail for each says the right thing —
  a supplement reports the **increment** (read back off its own `raw_payload`),
  an extension the **new end date**, a renewal the new period. None of them
  should say "is now active".

**R2.5 · An Adjustment.** Post one (the corpus has
`adjustment_uwis0064_manual.json`, or use `sam-admin xras --show`).
- **Look at:** the row appears as history with **no Notify button** and a `—` in
  the Notified column. That is deliberate: an Adjustment can be a *reduction*.

**R2.6 · The window and the chips.** 7D / 30D / 90D / Custom; then each chip.
- **Look at:** counts stay live when a chip is selected (self-exclusion — if
  every other count drops to 0 the chips have become dead ends); the window
  survives a Notify, because the container re-fetches a bare `hx-get` and only
  `hx-include` carries the filters through.

**R2.7 · The row expansion.** Expand a notified row.
- **Look at:** per-recipient status, time and any error inline; then click
  **Notify and Activate on that same expanded row** — the capture-phase problem
  breaks exactly this, and only for the buttons.

**R2.8 · Force.** Notify a row twice.
- **Look at:** the second attempt offers **Send again anyway** *only because* a
  duplicate would be suppressed; a row whose action is new must not show it.

**R2.9 · Dismiss.** Dismiss a project needing activation.
- **Look at:** the row **stays**, the Activate button goes, the reason is in the
  tooltip. Then Restore.

**R2.10 · Mobile.** 375px.
- **Look at:** the page must not scroll horizontally; the table scrolls inside
  its own wrapper. Known and unaddressed: the useful columns are three swipes
  right, and a card-per-row layout below `md` would read better. That is a
  redesign, not a fix — decide whether it is in scope.

**R2.11 · Teardown.** §11. Env only; data stays.

### Open questions Round 2 should answer

- ☑ **Is `update` the right kind for a Renewal?** **Yes** — closed in Round 2.
  `Renewal` against an existing project dispatched to `update`, rendered
  `xras_update.txt`, and read correctly ("has been renewed", allocations for
  the new period, project code and members unchanged).
- ☑ **Should an Adjustment notify after all?** **Yes** — built in Round 2 as
  `xras_adjustment`. See § *The Adjustment notice* below for the wording rule,
  which is the whole difficulty.
- ☐ **Does the window default of 30 days match how an operator works?** Triage
  week suggests days, not weeks. Still open.
- ☑ **Is the action-log table below now redundant on this page?** **No, keep
  it.** It shows *failures*, which the activity table cannot: that one is
  scoped to `status='processed'` by design. Surfacing failures is exactly what
  is wanted during deployment, when a 422 on an unknown contract or an
  unmapped resource is the thing an operator most needs to see.
- ☑ **Automatic sending** — **follow-on, not this branch.** Everything stays
  manual through the ledger for cutover. The message builders already take an
  action and are shaped for a handler to call.
- ☑ **The `--ncar-vermilion` alert contrast (3.84:1)** — **accepted.** Do not
  re-flag it.

### Round 2 findings — as run, 2026-08-10

Driven through Playwright against `webdev` (5050) with the `.env` block still
armed. Nine actions posted (`16`–`24`), four projects minted (`UHSS0003`–
`UHSS0005` plus the Round 1 pair), seven notices sent.

**All four kinds are now exercised end to end.** Each dedup key names its own
action, which is the correlation the whole ledger rests on:

| Kind | Action | Project | Wording checked |
|---|---|---|---|
| `xras_activation` | 16 | UHSS0003 | "is now active", four resources |
| `xras_supplement` | 18 | UHSS0003 | increment **and** new total; 10,000+2,500 → 12,500 ✓ |
| `xras_extension` | 19 | UHSS0003 | new end dates, "nothing else changes" |
| `xras_update` | 20 | UHSS0004 | "has been renewed" — **the open question is closed, `update` is right** |

**The redesign does what it was built to do.** Activating `UHSS0003` left the
row in place, flipped the badge to *Active*, turned Notify into *Notify again*,
and dropped the Activate button — with the chips re-counting live. Dismissing
`UHSS0005` likewise kept the row and swapped Activate for Restore. In Round 1
both rows would have vanished.

Two subtler rules confirmed by observation rather than by test: `UHSS0004`
carried a *Needs activation* Renewal row and a plain *Inactive* New row at the
same time — one Activate per project, on the latest action only — and the state
chips kept full-set counts while the action chips narrowed, which is the
self-exclusion that stops a chip becoming a dead end.

#### Seven things fixed during the run

1. **Two columns headed "Project".** The state column now reads **State**.
2. **The table overflowed its card by 99px on every desktop width**, putting
   *Activate* off-screen behind a scroll. The card is capped at 1388px
   regardless of viewport; eight columns plus a four-button action group wanted
   1487. **Recipients moved into the row expansion** (Ben's call), which
   reclaimed 159px and gave Title 58 of them. Now 0 overflow.
3. **Consequence of (2), handled:** rows with no notifications previously did
   not expand at all, which would have made the addresses unreachable on
   exactly the un-notified rows where an operator wants them. **Every
   manageable row now expands**; the Delivery table appears only when there is
   delivery to show.
4. **The one thing in Recipients that was a *problem* rather than a fact** — a
   notifiable action with nobody to mail — is promoted to a red *No recipients*
   badge in the Notified cell rather than buried in the expansion.
5. **`<code>` inside an alert measured 1.21:1** against 8.57:1 for the alert's
   own text. The string it hid most often is the redirect address — *"mail is
   going HERE, not to the PI"* — on this modal and on the admin Notifications
   card. Fixed globally in `components.css`; twelve templates benefit.
6. **htmx logged a console ERROR on every first-time send**, because the Send
   button's `hx-include="#xrasNotifyForce"` was unconditional while the force
   checkbox only renders on a re-notify. Now emitted only when the box exists.
7. **The Dismiss modal still promised to "hide the project from the
   pending-activation card"** — copy that outlived its behaviour by one
   redesign. Rewritten, and pinned by a test that fails if the old promise
   comes back.

#### One real ordering defect, and the decision taken

**The activation notice can be sent while the project is still inactive.** It
says *"is now active"* in as many words. Measured: the `UHSS0003` notice left
at **15:11:53**; the project was not activated until **15:12:57** — 64 seconds
during which the mail was false, and nothing would have forced the second step
at all.

Four options were weighed (warn / block / fold Notify into Activate / accept).
**Decision: warn, still allow** — an operator may legitimately be about to
activate, and a hard block makes a reasonable order of work impossible. The
Notify modal now opens with a danger banner above the redirect banner (the
message being *wrong* outranks where it is *going*), and it clears the moment
the project is active. Verified both ways.

#### Flagged, not fixed

- **`.alert-danger` is `--ncar-vermilion` on `--text-on-brand` at 3.84:1** —
  below AA for body text. Pre-existing, deliberate, and shared by twelve
  templates, so restyling it is a project-wide decision and not this branch's
  to take. The new banner is consistent with every other danger alert.
- **A pre-existing flaky test was found and fixed** (`test_status_chips_are_
  wired_to_the_filter_form`), because it failed 3 runs in 6 on *unmodified*
  code. Cause: the next test's `committed_odd_status_action` fixture commits
  its row — deliberately, since the route reads `db.session`'s own connection —
  and a committed row is visible to every other xdist worker, so the exact
  chip-count assertion saw a seventh chip. Now asserts the vocabulary is
  present rather than that nobody else exists. Six runs, six passes.
- **`update` rewrites the project title from the payload**, so `UHSS0004` is
  now titled "Smoke Test - Renewal of UHSS0004". That is the handler doing its
  job; only the synthetic payload is silly.
- **Mobile** (390px): the page body does not scroll horizontally (375 ≤ 390)
  and the table scrolls inside its own wrapper. The card-per-row redesign below
  `md` stays deferred.

Only one open question survives the list above: the 30-day default window.

### The Adjustment notice — the fifth kind

`adjust` was deliberately unmapped, on the grounds that an Adjustment can be a
**reduction** and "your allocation was cut" was not a mail to send before
deciding what it should say. Decided: **it notifies**, because a PI whose
allocation shrank is exactly who needs telling. `transfer` remains the only
unmapped service (it parks as `manual` and never completes).

The difficulty is entirely in the wording, and it is load-bearing:

- **The subject line claims no direction** — *"allocation has been adjusted"*.
  A subject promising good news is read long before the body can correct it.
- **The change is stated per resource with an explicit sign**, `+50,000` /
  `-100,000`, from `_action_increments(action, signed=True)`. The `+` is added
  only here; a supplement's amounts are increments by construction and its
  template already says "Added by this request".
- **`changes` is a separate context key from `added`.** `added` carries a
  promise that every number in it is an increase, which the supplement wording
  leans on. An adjustment makes no such promise, and reusing the key would
  have let a reduction render under supplement prose.
- **Units are computed on the magnitude.** `allocation_unit` picks
  singular/plural from the value, and −1 is one hour in either direction.
- The body ends with *"If this does not look right, please contact
  help@ucar.edu"* rather than the cheerful close the other four share.

`tests/unit/test_notify_templates.py::TestTheAdjustmentNoticeNeverPresumesADirection`
renders a **reduction** and fails if either part contains *additional*,
*added*, *increase*, *more time* or *extra*. It asserts on the **rendered**
part, not the source file, so a Jinja comment explaining the ban does not trip
its own rule.

**Both directions were smoked** against `UHSS0003`, the synthetic project — no
real allocation was touched:

| Posted | Derecho | Casper | Mail said |
|---|---|---|---|
| `--amount -100000` | 1,250,000 → 1,150,000 | 12,500 → 2,500 | `-100,000` / `-10,000` |
| `--amount 50000` | → 1,200,000 | → 7,500 | `+50,000` / `+5,000` |

`scripts/xras/smoke_payloads.py --adjustment PROJCODE --amount N` generates
them; the amount is signed and Casper takes a tenth of it, so one run exercises
two magnitudes.

**The below-zero guard was smoked too**, and it is the reason to keep the
action-log table. `--amount -9000000` came back 422 naming both resources —

> Adjustment of -9,000,000.00 for Derecho would take the allocation below zero
> (currently 1,200,000.00)

— and **wrote nothing**: Derecho and Casper were still 1,200,000 and 7,500
afterwards, so the whole action rolled back rather than applying the half that
fit. Legacy has no such guard, but legacy also never ran an Adjustment. The
failed row appears only in the action log below, never in the activity ledger,
which is exactly the split that makes both tables worth having during cutover.

### One Round 1 note corrected

The `UHSS0001` Supplement row reading "notified" was **right**, and an in-flight
suspicion that it was mis-keyed was wrong: dedup key `xras_activation:UHSS0001:14:…`
names action **14**, which is the Supplement. What is genuinely stale is only the
*kind* — that mail went out with activation wording because it predates the four
kinds. Re-notifying it now would produce the correct supplement text.

---

## Verification

The worked run is the verification. Afterwards, on the branch:

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
docker compose --profile test up -d mysql-test
pytest tests/unit/test_notify_*.py tests/unit/test_xras_*.py tests/api/test_xras_*.py -v
pytest              # full suite, ~90 s under xdist
pytest -m stress -n 0
```

A template edit changes no chart fingerprint and no route map, so a green suite plus
the run is the whole gate. If code changes land, re-run the full suite and record the
count here.
