# XRAS incoming → email — the pre-deploy smoke test on a local stack

> ☐ **In progress.** Branch `xras_incoming_smoke`, on top of `integration` at
> `dfaf7eb`. Findings and any code changes land here.

**Operational checklist.** Everything below is a command to run, a page to click or
an observation to record. It is worked *through* — tick `☐ → ✅` in place and write
what you saw under [`## Findings`](#findings). The value of this document is the
record, not the recipe.

**Companions:** [`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md) (what happens
after this passes) · [`NOTIFICATION_FRAMEWORK.md`](NOTIFICATION_FRAMEWORK.md)
(the mailer) · [`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md) (the contract)

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

---

## Preconditions

| ☐ | Precondition | How to prove it |
|---|---|---|
| ✅ | On `xras_incoming_smoke`, both PRs present | `git log --oneline -2` → `dfaf7eb`, `24965d9` |
| ✅ | The three tables exist locally, and are empty | `SHOW TABLES LIKE 'xras%'` / `LIKE 'notification%'` → `xras_action_log`, `xras_activation_event`, `notification_log`, all at 0 rows — a clean slate |
| ✅ | Containers up | `docker compose ps` → `webdev`, `webapp`, `mysql`, `mysql-test`, `cache` healthy |
| ✅ | Resource key mapping seeded | `SELECT COUNT(*) FROM xras_resource_repository_key_resource` → 13, incl. keys `145575` (Derecho), `145576` (Derecho GPU), `145145` (Data_Access), `144650` (Casper) — every key the corpus uses resolves |
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
# --- XRAS pre-deploy smoke (docs/plans/XRAS_PRE_DEPLOY_SMOKE.md) — remove after ---
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
then posts the 8 scrubbed production payloads plus a deliberate 400 and 422.

Some `New` actions will fail. That is the measured ~30 % success rate, and it is what
makes the queue realistic enough to judge the operator surface.

- **Done when** `/allocations/xras` renders a mixed queue and
  `SELECT status, COUNT(*) FROM xras_action_log GROUP BY status;` shows more than one
  status.

✅ Credential created and `ROLE_XRAS` linked; 10 rows written — **4 `processed`,
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

- **Done when** the row reads `status='redirected'`, `recipient='benkirk@ucar.edu'`
  and `intended_recipient` = the pre-redirect address; an `event_type='notified'` row
  exists; and the mail is in the inbox with the redirect banner.

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

**☐ 2 · Allocation amounts in the mail are rendered *compact*.** The single most
important finding of the run so far. The preview reads:

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

Recommended: render allocation amounts in this template with `fmt_number(raw=True)`
(or the `alloc_unit`-aware equivalent) so they always read `1,000,000`. Applies to
both `.txt` and `.html`.

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

**☐ 5 · The four processed corpus actions produced no card rows at all**, because
every project they touched (`UWIS0064`, `UCUB0166`, `UBRN0027`, `UCUB0182`) is
already active. So an Extension, an Adjustment and two Supplements against live
projects were applied and left **no operator-visible trace on the worklist**. This is
gotcha 1's corollary arriving unprompted, and it sharpens the design question: the
pending card is an *activation* worklist, not an *action* worklist, and there is
currently no surface that says "something changed on a project that is already
running". Whether that matters is Ben's call; the XRAS log page does record it.

**☐ 6 · No `New` action in the committed corpus can succeed on any local database.**
All three fail on the roster (`PI user_00000009 is not in database`) because
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
- *"This message replaces the activation notice previously sent by the NSF NCAR
  allocations office"* is transitional wording — right for cutover, but decide now
  whether it is meant to age out.

### Carried in as hypotheses

- **Notify is unreachable once a project is activated** (gotcha 1). If the intended
  operator order really is activate-then-notify, the card needs to retain
  recently-activated projects for a window, or Notify needs a second home on the
  project page.
- **No force on the operator Notify** (gotcha 2). Reasonable as anti-spam, but it
  makes a legitimate re-send — corrected address, fixed template — impossible without
  SQL.
- **A supplement to an active project is invisible** on the operator surface. May be
  correct by design; if not, it is a gap in the worklist.

## Template changes

*What changed in `src/sam/notify/templates/xras_activation.{txt,html}` and why.*

☐ *(none yet)*

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
