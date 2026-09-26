# Ticketing from SAM: the ithelp Jira API and a thin ticket-provider layer

Status: design record, 2026-09-26. Phase 0 shipped (#634). Phases 1-3 are
**being built on Ben's PAT** on branch `jira-ticket-provider-build`; section 8
is the build plan and supersedes the column-level details in section 5 where
they differ (the `external_ticket` table replaces the four columns). A bot
token later is a config swap.

## 1. Why

PR #630 files NUSD's handoff as one plain-text mail per account request into
Jira-by-email (`account_ticket`, `webapp/register/handoff_mail.py`, D22 in
`docs/plans/implemented/ACCOUNT_REGISTRATION.md`). SAM never learns the issue key,
so the Admin > Accounts row cannot link to the ticket and nobody can see NUSD's
progress from SAM. Ben minted a personal access token (PAT) for the help desk and
asked what it unlocks, and whether a provider abstraction is worth building given a
move to Jira Cloud in 18-24 months.

## 2. The instance, as probed on 2026-09-26

**The help desk is `https://ithelp.ucar.edu`, a Jira Service Management (JSM)
instance**, not `jira.ucar.edu` (a software-projects Jira that happens to run the
same 11.3.11 build). Both are `deploymentType: Server`, community license, REST
**API v2 only** (there is no v3 on-prem) plus the JSM `servicedeskapi`.

| Fact | Value |
|---|---|
| Project | **RC** "NCAR Research Computing", type `service_desk`, serviceDeskId **3** |
| NUSD's queue (id 38) | `project = RC AND resolution = Unresolved AND component in (NUSD)` plus open statuses |
| Default triage | NUSD is the default triage tier: a new RC request gets component NUSD without asking |
| CSG's queue (id 36) | same shape, `component = CSG` |
| Request types | General help (31), **Add a user (20)**, Allocations (18), Scratch quota increase (25), Core-hours refund (22), Report failed job (35), Having a problem (28), System issue (36), Install/update HPC software (38), Feedback (19) |
| "Add a user" fields | `summary` "User to be added" (required), `description` "Project code and contact information" (required), `attachment` "Class listing" (optional); `canRaiseOnBehalfOf: true`, `canAddRequestParticipants: true` |
| Service Request workflow | Support wait, Customer wait (category `new`); In Progress, Pending, Escalated (`indeterminate`); Resolved, Canceled, Closed (`done`) |
| Transitions from Support wait | Respond to customer 851, In progress 891, Escalate 921, Pending 871, Resolve 761 (needs `resolution`), Cancel 901 (needs `resolution`) |
| SLAs started by an API create | Time to first response 1 h, Time to resolution 40 h |
| Mail-handler tickets | arrive as Service Request with request type **"Having a problem"** and reporter = the From (RC-36399, Ben's January hand-written request) |
| The PAT | acts as Ben (`RC-AGENTS`), holds every RC permission including `MODIFY_REPORTER`, `TRANSITION_ISSUES`, `ASSIGN_ISSUES`; **never expires** |
| `createmeta` | both the classic and the 9.x endpoints answer 400/404 here; `GET /servicedeskapi/servicedesk/3/requesttype/20/field` is the working substitute |
| User lookup | `GET /rest/api/2/user/search?username=<email>` resolves ucar staff and the external customers the mail handler created (`margaret.duffy@utah.edu` is a real JSM customer) |

No ticket from the new SAM mail path had landed yet at probe time, so how the
mail handler maps SAM's subject and From is still to be observed on the first
real request.

### 2.1 What was exercised on the test ticket RC-40274

Created via `POST /rest/servicedeskapi/request` (`serviceDeskId 3`,
`requestTypeId 20`, `raiseOnBehalfOf benkirk`), then, each returning 2xx:

- `POST /rest/api/2/issue/{key}/comment` (customer-visible) and
  `POST /rest/servicedeskapi/request/{key}/comment` with `public: false`
  (agents-only internal note).
- `PUT /rest/api/2/issue/{key}` to add the label `sam-account-request`, swap the
  component NUSD -> CSG, and set the assignee.
- `POST /rest/api/2/issue/{key}/remotelink` with `globalId
  sam:account_request:<id>` pointing at Admin > Accounts pinned to the row.
- `POST /rest/api/2/issue/{key}/transitions` (891, In Progress) with a comment.
- JQL `project = RC AND summary ~ "\"SAM-AR-0\""` returned exactly RC-40274 and
  `"SAM-AR-01"` returned nothing, so the phase-0 token is a safe lookup handle.

The ticket was left assigned to Ben in the CSG queue, In Progress, to close from
the UI.

## 3. Capability map

| Capability | Endpoint | SAM use | Phase |
|---|---|---|---|
| Find the ticket a mail created | `GET /rest/api/2/search?jql=` | learn the key after the fact | 1 |
| Read status | `GET /rest/api/2/issue/{key}?fields=status` | `statusCategory.key == 'done'` is the workflow-rename-proof signal | 3 |
| Create a proper request | `POST /rest/servicedeskapi/request` | request type "Add a user", NUSD component by default, key returned synchronously | 2 |
| Raise on behalf of | `raiseOnBehalfOf: <email>` | the requester or sponsor becomes the JSM customer and gets portal mail | 2, NUSD's call |
| Internal note | `POST /servicedeskapi/request/{key}/comment` `public:false` | "SAM saw the account: username X" without mailing the customer | 3, optional |
| Remote link | `POST /issue/{key}/remotelink` | a sidebar link to the SAM row; the body already carries the URL | optional |
| Transition / resolve | `POST /issue/{key}/transitions` | NUSD owns the workflow | out |
| Set reporter, assignee | v2 `PUT` | triage is NUSD's | out |
| Webhooks (push) | `/rest/webhooks/1.0/webhook` | Jira admin only, no request signing on Server, needs an inbound route | out; poll |

## 4. Design verdict

### 4.1 Standalone provider, not a notify channel

Two designs were compared against the code.

**Jira as a `sam.notify` Transport** is a bad fit. `Notifier` holds one transport
chosen by `NOTIFY_TRANSPORT`; `_redirected` rewrites every recipient address with
no channel awareness (a ticket "address" would become an email and 400);
`_addressed` fills cc/bcc/sender onto every account-family message and the ledger
records them as having left; `notification_log.transport` is written from the
config string, not the delivering transport; `deliver()` returns nothing by
contract so the key would need a ledger column. Roughly 370 lines across 14
framework files, and every row of the CLAUDE.md Notifications table gains an
"except tickets" clause.

**A standalone `src/sam/integration/tickets/`** in the XRAS-client shape wins:
about 150 lines of glue beyond the client, nothing inside `sam/notify`, and the key
stored on the request row next to `invite_sent_at`, `completed_at`, `fulfilled_at`,
which is how every other request fact is stored. It reuses three notify pieces by
direct call: the builder and renderer (`get_notifier(read_only=True).preview()`
honors template overrides), the ledger's `record(queued)` then
`resolve(sent | failed)` protocol, and the same `dedup_key`, so `already_sent`
covers the mail era and the API era with one predicate.

### 4.2 How general, for a two-year horizon

- Keep a `TicketProvider` ABC with **exactly one implementation today**, shaped
  so a second provider is an additional class (six abstract members; `find`,
  `comment`, `check` have safe defaults) and never a change to the Jira one.
  It earns its keep for the test fake, because the callers never import
  `requests`, and because Ben wants leads and admins raising other RC request
  types (allocations, scratch quota, refunds) from Manage Project later.
- Do **not** pre-build a Cloud provider. JSM Cloud exposes the same
  `/rest/servicedeskapi/request` shape, so the create path barely moves. The
  delta is auth (Basic `email:api_token` instead of Bearer), the base URL, and
  the v2 search endpoint (`/rest/api/2/search` is retired on Cloud for
  `/rest/api/2/search/jql`). That is a config flag plus one method, about 30 lines.
- **Store the link in a small `external_ticket` table**, not columns on
  `account_request` (Ben's decision, 2026-09-26): one row per ticket, keyed by
  provider + ticket key, polymorphic `entity_type`/`entity_id` like
  `notification_log`, so project- and allocation-level requests can use it
  later without a second DDL handoff and a backfill. Still no stored URL: it is
  `provider.browse_url(key)` at render time, so a migration touches zero rows.
- Do **not** add `notification_log.external_ref`. Revisit when a second kind
  needs an external reference.

## 5. Phases

| Phase | What | Schema | CronJob env | Status |
|---|---|---|---|---|
| 0 | `[SAM-AR-<id>]` token in the mail subject | none | none | **shipped with this doc** |
| 1 | read-only client; learn the key hourly; `RC-40274`-style link on the Accounts card | `external_ticket` table | read keys + token | building, section 8 |
| 2 | JSM create with mail fallback behind `TICKET_PROVIDER=jira-servicedesk`, internal automation note on every created ticket | none | none | building on Ben's PAT; bot token later |
| 3 | status sync | none | none | building, section 8 |

### Phase 0: the subject token

`ticket_subject()` in `sam/queries/account_notices.py` appends ` [SAM-AR-<id>]`;
`subject_token()` beside it is the one place the format lives. Jira-by-email keeps
the summary verbatim, so every ticket filed from this deploy on is findable by
`summary ~ "\"SAM-AR-<id>\""` (verified, section 2.1). NUSD sees a short suffix
and nothing else changes.

### Phase 1: learn the key, show the link

Package `src/sam/integration/tickets/`:

- `base.py`: `TicketDraft(token, summary, body, link_url, labels)`,
  `TicketRef(key, url, status='', closed=None)`, `TicketSourceUnavailable` >
  `TicketNotConfigured`, `TicketRejected(status, errors)` (the XRAS three-outcome
  rule: value, `None`, raise), `TicketProvider` ABC with `create`, `find(token)`,
  `get(key)`.
- `config.py`: frozen `JiraConfig.from_environment()` copying the Flask-then-env
  seam of `sam/integration/xras_api/config.py`. Keys: `JIRA_ENABLED` (off),
  `JIRA_WRITE_ENABLED` (off, webapp-only, never in `cronjob-tasks.yaml`),
  `JIRA_BASE_URL` (`https://ithelp.ucar.edu`), `JIRA_TOKEN`, `JIRA_PROJECT_KEY`
  (`RC`), `JIRA_SERVICE_DESK_ID` (`3`), `JIRA_REQUEST_TYPE_ID` (`20`),
  `JIRA_LABELS` (`sam-account-request`), `JIRA_AUTH` (`bearer` | `basic`, the
  Cloud seam), `JIRA_USER` (Cloud only), timeouts and retries as XRAS.
  `summary()` reports `token_set`, never the token.
- `jira.py`: a private transport in the `_XrasTransport` shape (own class; the
  XA-* headers mean nothing here). Reads retry 5xx with backoff, 404 is `None`,
  other 4xx raise `TicketRejected` (401 says "token rejected"). **Create is one
  attempt**: a retried create is a duplicate ticket. Body wrapped in `{noformat}`
  so aligned columns survive wiki rendering (the SAM-request URL goes outside the
  block so it stays clickable). `find` runs
  `project = RC AND summary ~ "\"SAM-AR-<id>\"" ORDER BY created ASC`, oldest
  wins, two hits logged at WARNING (the cutover-duplicate detector).
- `learn.py`: `learn_ticket_keys(session, provider, *, clock, limit)` over rows
  with `ticket_key IS NULL`, a ticket sent (ledger `account_ticket:<id>` in
  `sent`), not synced in 6 h, younger than 60 d, capped by
  `SAM_TASKS_TICKET_LOOKUP_MAX` (25). Fail-open twice, per
  `xras_api/comments.py`: unconfigured returns `skipped` before any row;
  unavailable mid-loop stops, keeps stamps, reports. Never raises past a row.

Wiring:

- `sam/core/account_requests.py`: `ticket_key VARCHAR(32)`, `ticket_status
  VARCHAR(32)`, `ticket_closed_at DATETIME`, `ticket_synced_at DATETIME`, one
  ALTER (`scripts/sql/alter_account_request_ticket.sql`, the
  `alter_account_request_invites.sql` idiom) so it is one DBA handoff; the same
  columns appended to `create_account_request.sql` because the test bootstrap only
  creates. Pin in `tests/integration/test_schema_validation.py`. `ticket_key` is
  not redacted by `dbbrowse/redact.py`; never name a column `ticket_token`.
- `scheduling/tasks/account_requests_reconcile.py`: call `learn_ticket_keys` after
  the reconcile and merge its counts into `detail`; update the "DB-only" docstring.
- `sam/queries/account_requests.py` `request_views`: `ticket_key`, `ticket_url`.
- `templates/dashboards/admin/fragments/account_requests_card.html`: the monospace
  key link beside `sent <date>` in the status cell, a Ticket row in the detail
  list; a `ticket_link` macro in `fragments/account_request_bits.html` so the
  Invitations tab reuses it.
- `webapp/utils/config_inspect.py` and `configuration_card.html`: a `tickets`
  block from `JiraConfig.summary()`.
- Helm: `values.yaml` `JIRA_*` keys plus a `jiraCredentials` block mirroring
  `xrasApiCredentials` (OpenBao path, key `token`); `external_secret.yaml` and
  `deployment.yaml` secretKeyRef; `cronjob-tasks.yaml` hand-lists the read keys
  and the token, never `JIRA_WRITE_ENABLED`; `values-dev.yaml` `JIRA_ENABLED: "0"`
  and `jiraCredentials.enabled: false`; `helm/tests/test-cronjob-render.sh`
  asserts each against the `-s` render.

Tests: `pytest_configure` assigns `JIRA_TOKEN=''`, `JIRA_ENABLED='0'`,
`JIRA_WRITE_ENABLED='0'` (assign, not setdefault, so a developer `.env` cannot
leak the PAT in); `test_outbound_guards.py` gains the pins test; a
`FakeTicketProvider` in the tests tree; provider tests mock
`client.session.request` and pin the create body and the JQL string; learn tests
cover hit, miss, throttle, cap, unconfigured and mid-loop failure; gates copy
`TestHelmWriteLever` for the CronJob env and dev values; schema pin +4.

### Phase 2: JSM create replacing mail

Prerequisite: a Jira service user with Browse and Create on RC, its PAT in
OpenBao. Not Ben's PAT: it never expires, which is worse for offboarding, and
every ticket would carry him as reporter.

`send_ticket` grows one branch. `TICKET_PROVIDER != 'jira'` or write unconfigured
falls to today's mail path. Otherwise: `ledger.already_sent(dedup_key)` returns;
`provider.find(token)` hit stamps the key and records `sent`; else
`ledger.record(queued, transport='jira')`, `provider.create(draft)`,
`ledger.resolve(sent)`, stamp `ticket_key` on its own short session (a filed
ticket must survive a later rollback); on `TicketSourceUnavailable` or
`TicketRejected`, resolve `failed` and **fall back to mail**. Add
`Channel.TICKET` to the enum. Webapp timeouts 3.05 / 5 s, one attempt. Never call
the provider inside `management_transaction`; all six call sites are already
after commit.

Create goes through `POST /rest/servicedeskapi/request` with
`requestTypeId 20`, so NUSD sees a proper "Add a user" request in their queue
instead of the mail handler's "Having a problem". Three open questions for NUSD:

1. **Who is the customer.** `raiseOnBehalfOf` can name the requester's email
   (JSM creates the customer and mails them portal notices) or the sponsor. The
   mail path makes Ben the reporter. SAM already sends the requester a receipt, so
   the default is the service user as reporter with the sponsor as a request
   participant; NUSD may prefer the requester.
2. **Component.** The default triage tier sets NUSD; no need to send it.
3. **Attachment.** "Class listing" exists for workshops; an event roster could
   attach itself later.

Cutover dedup is three layers: the ledger key, find-before-create, and the phase-1
two-hit warning. The residual window is a mail already in the relay but not yet
ingested at the flip; flip at a quiet hour.

### Phase 3: status sync and the internal note

`learn.py` gains a refresh for rows with a key, not closed, not synced in 6 h:
`provider.get(key)`, stamp `ticket_status` and, the first time
`statusCategory == done`, `ticket_closed_at`. Value is marginal: `ticket_closed_at`
is nearly `fulfilled_at`. The one new fact is "NUSD closed it without an account
appearing", which the card can render as a warning. Ship only if that case occurs.

An internal note on fulfillment (`public: false`) is the only write a scheduled
task would ever make; the house rule keeps write levers out of CronJob pods, so
it needs its own deliberate decision and drift gate.

## 6. The Cloud seam

Changes: `JIRA_AUTH=basic` plus `JIRA_USER`, `JIRA_BASE_URL`, the search path and
its paging shape. Unchanged: `servicedeskapi` create and comment, stored keys,
the dedup key, the subject token, the ABC, the card, the ledger. If UCAR changes
trackers rather than Jira editions, the ABC is the seam and `ticket_key`'s format
dates the row.

## 7. Open items

- Watch the first real SAM mail ticket land: which request type and reporter the
  handler assigns, and that the `[SAM-AR-<id>]` suffix survives.
- Ask for a Jira service account for RC; swapping it in is a change to the
  OpenBao value only.
- Ask NUSD who the customer should be on an API-filed request
  (`TicketDraft.on_behalf_of` exists, nothing sets it).
- Close RC-40274 from the UI.

## 8. Build plan (handoff, 2026-09-26)

Approved plan for phases 1-3. One PR against staging from branch
`jira-ticket-provider-build`, one commit per build step, on Ben's PAT.

### 8.0 Prompt for the build session

> Read `docs/plans/TICKET_PROVIDER.md` on branch `jira-ticket-provider-build`
> end to end, especially § 8 "Build plan (handoff)", and follow its build order
> one commit at a time on that branch. The desk is Jira Service Management at
> `ithelp.ucar.edu` (project RC, service desk 3, request type 20); the PAT is
> line 4 of `~/jira_token` and is for local testing only, never committed. Build
> `src/sam/integration/tickets/` (ABC + registry + `JiraServiceDeskProvider`,
> composition over a private transport), the `external_ticket` table
> (`scripts/sql/create_external_ticket.sql`, ORM `ExternalTicket`, bootstrap
> tuple, schema pin), the hourly learn/refresh in `account_requests_reconcile`,
> the provider branch in `send_ticket` with mail as fallback and the internal
> automation note, the Accounts card link, the Configuration tile, and the Helm
> `jiraCredentials` wiring with `JIRA_WRITE_ENABLED` and `TICKET_PROVIDER` kept
> out of the CronJob. Rehearse the DDL on the local MySQL and the :3307 test
> container, then stop and give Ben the exact command to apply it to prod
> before opening the PR against staging. Pin `JIRA_TOKEN=''` and the three
> levers in `pytest_configure`. When green, open one PR and update the doc to
> as-built. Open items to leave in the doc: bot account, JSM customer choice,
> close RC-40274.

### 8.1 Configuration and modes

Three fail-closed levers; nothing else selects behavior.

| Lever | Default | Meaning |
|---|---|---|
| `JIRA_ENABLED` | off | reads allowed (learn, status, check) |
| `JIRA_WRITE_ENABLED` | off | creates and comments allowed; **webapp-only**, never in `cronjob-tasks.yaml` |
| `TICKET_PROVIDER` | `''` | `''`/`mail` = today's mail path; `jira-servicedesk` = file through the API, mail on any failure |

Provider-owned keys (`JIRA_*`): `JIRA_BASE_URL` (`https://ithelp.ucar.edu`),
`JIRA_TOKEN` (secret), `JIRA_AUTH` (`bearer` | `basic`), `JIRA_USER` (Basic
only), `JIRA_PROJECT_KEY` (`RC`), `JIRA_SERVICE_DESK_ID` (`3`),
`JIRA_REQUEST_TYPE_ID` (`20`), `JIRA_LABELS` (`sam-account-request`),
`JIRA_TIMEOUT` / `JIRA_CONNECT_TIMEOUT` / `JIRA_MAX_RETRIES` (10 / 3.05 / 3, the
webapp create path overrides to 5 / 3.05 / 1).

Modes: (a) mail only = all three off, unchanged from today; (b) mail plus
read-only = `JIRA_ENABLED=1`; (c) API create with mail fallback = all three on.
Production ships in (c) on Ben's PAT; `values-dev.yaml` ships in (a) with the
credential block disabled.

### 8.2 Package `src/sam/integration/tickets/`

Follows `sam/integration/xras_api/` (config seam, transport, exceptions) and
`sam/integration/awards/` (provider ABC + registry). Nothing under `sam/notify`
changes except one enum member.

- `base.py`: `TicketDraft(token, summary, body, link_url, labels=(),
  on_behalf_of='', automation_note='')`, `TicketRef(key, url, status='',
  closed=None)`, exceptions `TicketSourceUnavailable` > `TicketNotConfigured`,
  `TicketRejected(status, errors)`, and `DEFAULT_AUTOMATION_NOTE`.
  `TicketProvider(ABC)`, `name: ClassVar[str]`:
  - **abstract** (the surface the registry, card and callers use without
    knowing the class): `from_environment()` classmethod (never raises; check
    `configured`), `configured`, `write_configured`, `summary()` (never the
    credential), `create(draft) -> TicketRef`, `get(key) -> Optional[TicketRef]`,
    `browse_url(key)`.
  - **concrete defaults** (optional capabilities, so a second provider is six
    members): `find(token)` returns `None`; `comment(key, text, *,
    internal=True)` raises `NotImplementedError`; `check()` returns `(True, '')`;
    `guard_write()` raises `TicketNotConfigured` unless `write_configured`.
  - Provider invariant: `create` posts the automation note (`draft.automation_note
    or DEFAULT_AUTOMATION_NOTE`) as an internal comment after the key is minted,
    wrapped so a failed note never fails the create. No `ProviderConfig` base
    class: each provider owns its frozen config dataclass; the three abstract
    properties are the whole shared surface.
- `sam/integration/_config.py`: `raw`, `config_str`, `config_bool`,
  `config_int`, `config_float` lifted from `xras_api/config.py` (xras
  semantics: positive-int guard, `ImportError` caught); `xras_api/config.py`
  repointed to import them (nothing patches them by path). The copies in
  `notify/config.py`, `queries/allocation_state.py`, `caching/buckets.py` are
  left for a later sweep and named in the module docstring.
- `jira.py`: `JiraConfig` frozen dataclass (`from_environment`, `configured`,
  `write_configured`, `summary`; default `timeout=5`); `_JiraTransport`
  (module-private; persistent `requests.Session`, Bearer or Basic from
  `config.auth`, `(connect, read)` timeouts; `get` retries 5xx/socket with
  `2**attempt` backoff, 404 → `None`, other 4xx → `TicketRejected`; `post` is
  **one attempt**, 4xx → `TicketRejected(status, errors)` from
  `errorMessages`+`errors`, else `TicketSourceUnavailable`; 401/403 message says
  "token rejected"); `JiraServiceDeskProvider(TicketProvider)` **composes** the
  transport (constructor arg, so tests inject a fake without `requests`; a
  future `JiraSoftwareProvider` reuses it the same way, no inheritance between
  providers). `create` = `guard_write()`, `POST /rest/servicedeskapi/request`
  (`serviceDeskId`, `requestTypeId`, `requestFieldValues{summary, description}`,
  `raiseOnBehalfOf` only when `on_behalf_of` is set) → `TicketRef` from
  `issueKey`; labels via a best-effort `PUT /rest/api/2/issue/{key}` only when
  `config.labels` is non-empty (JSM rejects unknown request fields); then the
  automation note. `comment` = `POST /rest/servicedeskapi/request/{key}/comment`
  `{public: not internal}`; `get` = `GET /rest/api/2/issue/{key}?fields=status`,
  `statusCategory.key == 'done'` → `closed`; `find` = `GET /rest/api/2/search`,
  `project = <key> AND summary ~ "\"<token>\"" ORDER BY created ASC`, oldest
  wins, two hits logged at WARNING; `check` = `GET /rest/api/2/myself` →
  `(True, displayName)`; `browse_url` = `<base>/browse/<key>`. The description
  is `{noformat}\n<body>\n{noformat}\n\nSAM request: <link>` so the aligned
  columns survive wiki rendering and the link stays clickable; the caller never
  sees wiki markup.
- `registry.py`: `PROVIDERS = {JiraServiceDeskProvider.name:
  JiraServiceDeskProvider}`, `MAIL_NAMES = {'', 'mail', 'none'}`,
  `build_provider(name) -> Optional[TicketProvider]` (mail names → `None`;
  unknown name raises `TicketNotConfigured` listing the valid names, like
  `notify.registry.build_transport`), `provider_from_environment()` reading
  `TICKET_PROVIDER`, `provider_names()` for the card and the gate. `learn` and
  `status` also go through `provider_from_environment()`, so
  `TICKET_PROVIDER=mail` means Jira is not in play at all; "pause creates but
  keep learning" is `JIRA_WRITE_ENABLED=0`, the XRAS write-lever discipline.
- `learn.py`: `learn_ticket_keys(session, provider, *, clock, limit)` and
  `refresh_ticket_status(session, provider, *, clock, limit)`. Both fail-open
  twice (`xras_api/comments.py` pattern): `TicketNotConfigured` → `skipped`
  before any row; `TicketSourceUnavailable` mid-loop → stop, keep stamps,
  report. Selection: learn = `account_request` rows with a `sent`
  `account_ticket:<id>` ledger row, no `external_ticket` row for
  (`account_request`, id), `creation_time` within 60 d; a miss is remembered in
  a process-local dict for the run only, so a mail the handler has not ingested
  yet is retried next hour and a row is never written for a miss. Refresh =
  `external_ticket` rows with `closed_at IS NULL` and `synced_at` older than
  6 h. Cap `SAM_TASKS_TICKET_LOOKUP_MAX` (25) on each. Inserts and stamps go
  through `ExternalTicket.create()` / `mark_synced()` with
  `requested_by='task:account_requests_reconcile'`.
- `__init__.py`: exports.

### 8.3 Data model: the `external_ticket` table (Ben's decision, 2026-09-26)

One row per ticket SAM filed or learned, polymorphic over the SAM entity so
project- and allocation-level requests can use it later. No FKs (the
`notification_log` / `account_request` convention); no URL (derived by
`provider.browse_url(key)` at render time).

| column | type | notes |
|---|---|---|
| `external_ticket_id` | INT PK auto | |
| `provider` | VARCHAR(32) NOT NULL | registry name, `jira-servicedesk` |
| `ticket_key` | VARCHAR(32) NOT NULL | `RC-40274`; not redacted by `dbbrowse/redact.py` (never name it `*_token`) |
| `entity_type` | VARCHAR(32) NOT NULL | `account_request` today |
| `entity_id` | INT NOT NULL | |
| `origin` | VARCHAR(16) NOT NULL | `created` (API) or `learned` (JQL after mail) |
| `requested_by` | VARCHAR(35) NOT NULL | username, `self`, or `task:account_requests_reconcile` |
| `status` | VARCHAR(32) NULL | tracker's status name, display only |
| `closed_at` | DATETIME NULL | first sync that saw `statusCategory = done` |
| `synced_at` | DATETIME NULL | last successful `get`/`find` |
| `creation_time` | DATETIME NOT NULL | app clock, naive Mountain (no `CURRENT_TIMESTAMP` default, per house DDL) |

Indexes: unique `(provider, ticket_key)`; `(entity_type, entity_id)`. The
"one ticket per request" rule stays in the ledger `dedup_key`; a second row for
the same entity is legal (a learned duplicate is the cutover detector, shown
oldest-first).

- ORM `ExternalTicket` in `src/sam/integration/tickets/models.py` (SQLAlchemy
  only, `SessionMixin`, `create()` classmethod, `mark_synced()` instance
  method), exported from `sam/__init__.py`. ⚠️ `tickets/__init__.py` must stay
  import-light (base + models only; `registry`/`jira` imported by path by their
  callers) so `sam/__init__.py` does not pull `requests` into every ORM
  consumer; extend `tests/unit/gates/test_notify_import_graph.py` with the same
  assertion for `sam.integration.tickets.jira`.
- `scripts/sql/create_external_ticket.sql` in the
  `create_xras_remediation_event.sql` idiom (`CREATE TABLE IF NOT EXISTS`,
  InnoDB, utf8mb3 with no human-text columns to widen, verification SELECT,
  "NO DROP" header). One tuple added to `_BOOTSTRAP_TABLES` in
  `tests/conftest.py`; pin in `tests/integration/test_schema_validation.py`.
- Query helpers in `tickets/queries.py`: `tickets_for(session, entity_type,
  ids) -> Dict[int, List[ExternalTicket]]` (oldest first) used by
  `request_views`, and the learn/refresh selections.
- Rehearse locally first: apply the script to the dev MySQL
  (`mysql -u root -h 127.0.0.1 -proot sam < scripts/sql/create_external_ticket.sql`)
  and to the test container on :3307, twice each, to prove idempotency and read
  the verification SELECT; then run `tests/integration/test_schema_validation.py`.
- ⚠️ Prod needs the table **before** the code deploys. After the local
  rehearsal and before the PR opens, stop and ping Ben with the exact `mysql ...
  < scripts/sql/create_external_ticket.sql` command and the expected
  verification output; Ben applies it. Also on the Postgres dev DB (`sam_dev`,
  per `docs/plans/K8S_DEV_ENVIRONMENT.md`) if samuel-dev should carry the table.

### 8.4 What Ben does (outside the repo)

1. **OpenBao**: a KV secret at `csg/sam-jira-token` with one field, `token`,
   holding the 44-character PAT from `~/jira_token` line 4. The chart's
   `jiraCredentials` block references it as `secretPath: csg/sam-jira-token`,
   `tokenKey: token`, `secretStoreRefName: csg-ro`, exactly the
   `xrasApiCredentials` shape. When the bot account arrives, only this value
   changes.
2. **Prod DDL**: apply `scripts/sql/create_external_ticket.sql` when pinged (build step 2).
3. **Close RC-40274** from the UI when convenient.

### 8.5 Filing path (`webapp/register/handoff_mail.py`)

`send_ticket(row)` keeps its signature and its six call sites (all after
commit). Internally:

1. `provider = provider_from_environment()`; if `None` or not
   `write_configured` → today's mail path, unchanged.
2. `ledger = get_notifier().ledger`; `already_sent('account_ticket:<id>')` →
   return `None` (one ticket per row, either era).
3. `provider.find(subject_token(id))` hit → insert an `ExternalTicket` row
   with `origin='learned'`, record a ledger row `sent` with
   `transport=provider.name`, `channel='ticket'`; return.
4. `message = build_ticket_message(...)`, `rendered =
   get_notifier(read_only=True).preview(message)` (template overrides honored).
   `log_id = ledger.record(message, status='queued', transport=provider.name)`.
5. `ref = provider.create(TicketDraft(...))`. On `TicketSourceUnavailable` or
   `TicketRejected`: `ledger.resolve(log_id, 'failed', detail)`, log, **fall
   back to the mail path** and return its result.
6. Success: `ledger.resolve(log_id, 'sent', detail=ref.key)`; insert the
   `ExternalTicket(origin='created', status=ref.status, synced_at=now)` row in
   its own short session (a filed ticket must survive any later rollback). The
   internal note was already posted by the provider inside `create` (its
   invariant).
7. Return a `DeliveryResult(status='sent', ...)` so callers are unchanged.

The draft's `automation_note` is built in `handoff_mail.py` from
`DEFAULT_AUTOMATION_NOTE` plus the row-specific line: "Filed automatically by
SAM (NSF NCAR Systems Accounting Manager) using Ben Kirk's API token, not by
hand. Replies here reach the NUSD queue, not SAM; the request lives at <link>."
The reporter stays the token owner, as with mail today; `on_behalf_of` is left
empty until NUSD says who the customer should be (open item in the doc).

`Channel.TICKET = 'ticket'` added in `sam/notify/base.py`; `Recipient` for the
ledger row uses `address=<project key>`, `channel=Channel.TICKET`. Never call
the provider inside `management_transaction` (docstring rule).

### 8.6 Hourly task and the card

- `scheduling/tasks/account_requests_reconcile.py`: after the reconcile, build
  `provider_from_environment()` and call `learn_ticket_keys` then
  `refresh_ticket_status` with `ctx.sam_session`, merging their counts into
  `detail` and `message`. Reads only, so no `dry_run` branch is needed (the
  stamps roll back under the runner). Docstring loses "DB-only".
- `sam/queries/account_requests.py` `request_views`: add `tickets`, a list of
  `{key, url, status, closed_at, origin}` from one `tickets_for()` call per
  page (provider built once per call for `browse_url`; `None` provider → empty
  URL, key still shown). Oldest first; the card shows the first and counts the
  rest.
- `templates/dashboards/admin/fragments/account_requests_card.html`: a
  monospace key link after `sent <date>` in the status cell; a Ticket row
  (key, status, closed date, origin) in the detail list; a warning badge when
  the ticket is closed and `fulfilled_at` is not set ("closed without an
  account"). A `ticket_link` macro in `fragments/account_request_bits.html` so
  the Invitations tab shares it.
- `webapp/utils/config_inspect.py` `gather_runtime_state`: a `tickets` block
  from `provider.summary()` (`provider`, `enabled`, `write_enabled`,
  `token_set`, `base_url`, `project`, `request_type`), rendered as a new tile in
  `configuration_card.html` with the `stat()` macro; `unavailable` fallback like
  the notifications block.

### 8.7 Helm and secrets

- `values.yaml` `webapp.env`: `TICKET_PROVIDER: "jira-servicedesk"`,
  `JIRA_ENABLED: "1"`, `JIRA_WRITE_ENABLED: "1"`, `JIRA_BASE_URL`,
  `JIRA_PROJECT_KEY: "RC"`, `JIRA_SERVICE_DESK_ID: "3"`,
  `JIRA_REQUEST_TYPE_ID: "20"`, `JIRA_LABELS`. A `jiraCredentials` block
  mirroring `xrasApiCredentials` (`secretStoreRefName: csg-ro`, `secretPath:
  csg/sam-jira-token`, `tokenKey: token`). Ben stores the PAT in OpenBao at that path.
- `external_secret.yaml`: a `jiraCredentials` ExternalSecret; `deployment.yaml`:
  `JIRA_TOKEN` secretKeyRef, and `jiraCredentials.enabled` added to the `or`
  gate on L56.
- `cronjob-tasks.yaml`: hand-list `JIRA_ENABLED`, `JIRA_BASE_URL`,
  `JIRA_PROJECT_KEY`, `JIRA_SERVICE_DESK_ID`, `JIRA_REQUEST_TYPE_ID`, `JIRA_AUTH`
  and the `JIRA_TOKEN` secretKeyRef under the XRAS-style WARNING; **never**
  `JIRA_WRITE_ENABLED` or `TICKET_PROVIDER`.
- `values-dev.yaml`: `TICKET_PROVIDER: ""`, `JIRA_ENABLED: "0"`,
  `JIRA_WRITE_ENABLED: "0"`, `jiraCredentials.enabled: false`.
- `helm/tests/test-cronjob-render.sh`: assert each read key and the secret name
  against the `-s` render; `helm/tests/test-dev-render.sh`: dev holds no token
  and files no API ticket.
- `.env.example`: the `JIRA_*` and `TICKET_PROVIDER` block, all off.

### 8.8 Tests

- `tests/conftest.py` `pytest_configure`: assign `JIRA_TOKEN=''`,
  `JIRA_ENABLED='0'`, `JIRA_WRITE_ENABLED='0'`, `TICKET_PROVIDER=''` (assign,
  not setdefault, so a developer `.env` cannot leak the PAT). The existing
  `_no_outbound_http` guard already blocks `ithelp.ucar.edu`.
- `tests/unit/models/test_outbound_guards.py`: the four pins added to the
  levers parametrize; the socket-guard message test also names `ithelp.ucar.edu`.
- `tests/unit/tickets/` (new domain dir; marker auto-derived; register `tickets`
  in `pytest.ini` and `pytest_collection_modifyitems` if the dir list is
  explicit): `FakeTicketProvider(TicketProvider)` in the domain `conftest.py`
  (dict-backed, records `created`/`comments`, `raise_with` attribute) and a
  `MinimalProvider` implementing only the six abstract members, together the
  extensibility proof; `test_registry_gate.py` (every registered class is
  concrete and `cls.name` is its key; `from_environment` under the pins yields
  `configured=False` and `create` raises `TicketNotConfigured`; mail names →
  `None`; unknown name raises naming the valid ones; `MinimalProvider()`
  instantiates);
  `test_jira_provider.py` (mock `client.session.request`, house idiom: create
  201 → `TicketRef` with derived URL and the pinned JSM body; 400/401 →
  `TicketRejected`; 5xx on create is one attempt; `get` 404 → `None`; `done`
  category → `closed=True`; `find` 0/1/2 hits and the JQL string pinned;
  internal comment body `public:false`; GET retries; Basic vs Bearer header);
  `test_learn.py` (hit inserts a `learned` row, miss writes nothing, throttle,
  cap, unconfigured, mid-loop failure, refresh stamps `closed_at` once and
  `synced_at` every time); `test_models.py` (`ExternalTicket.create`, unique
  `(provider, ticket_key)` raises on a duplicate).
- `tests/unit/webapp/test_account_registration.py` (+ builders): `send_ticket`
  with a `FakeTicketProvider` files once, stamps the key, writes ledger
  `transport='jira-servicedesk'`, posts the internal comment; a `find` hit skips
  create; `TicketSourceUnavailable` falls back to mail and the ledger shows
  both rows; `already_sent` short-circuits; provider `None` is byte-identical to
  today (existing tests pass unchanged).
- `tests/unit/tasks/test_task_account_requests_reconcile.py`: detail carries the
  learn/refresh counts; unconfigured leaves existing counts untouched.
- `tests/unit/webapp/test_admin_account_requests_routes.py`: card renders the
  link and the "closed without an account" badge.
- Gates: a `TestHelmJiraLevers` copying `TestHelmWriteLever` (values.yaml armed
  values pinned; `JIRA_WRITE_ENABLED`/`TICKET_PROVIDER` absent from `tasks.env`
  and the comment-stripped `cronjob-tasks.yaml`; dev values off); the
  `external_ticket` schema pin; `test_docs.py` for the doc edits.

### 8.9 Build order (one commit each)

1. `sam/integration/_config.py` lift + `tickets/base.py`, `config.py`,
   `registry.py`, `jira.py` + unit tests (no callers yet).
2. `ExternalTicket` model + `create_external_ticket.sql` + bootstrap tuple +
   schema pin + `tickets/queries.py` + `request_views` keys; local DDL
   rehearsal; **stop and ping Ben to apply the table to prod**.
3. `learn.py` + reconcile task wiring + task tests.
4. `send_ticket` provider path + `Channel.TICKET` + internal comment + webapp
   tests.
5. Card, bits macro, configuration tile + route tests.
6. Helm, `.env.example`, conftest pins, gates, `helm/tests` assertions.
7. `docs/plans/TICKET_PROVIDER.md` updated to as-built (phases 1-3 shipped,
   PAT posture, open items: bot account, JSM customer, close RC-40274);
   CLAUDE.md § Account family one line; D22 unchanged.

Rough size: ~900 LOC product, ~700 LOC tests.

### 8.10 Verification

- `pytest tests/unit/tickets tests/unit/webapp/test_account_registration.py
  tests/unit/webapp/test_account_requests_builders.py
  tests/unit/webapp/test_admin_account_requests_routes.py tests/unit/tasks
  tests/unit/gates tests/integration/test_schema_validation.py`, then the full
  suite; `bash helm/tests/test-cronjob-render.sh` and `test-dev-render.sh`.
- Live, on webdev with Ben's PAT in `.env` and `TICKET_PROVIDER=jira-servicedesk`:
  operator Verify on a test request files an RC ticket titled
  `[SAM API TEST]`-free but tagged `[SAM-AR-<id>]`, the internal note appears,
  the Accounts card shows the key; then mark the row fulfilled and run
  `sam-admin tasks --run account_requests_reconcile --force` to see the status
  refresh. Ben cancels the ticket from the UI.
- Prod rollout order: OpenBao secret → ALTER → merge/deploy → watch the first
  real request → `sam-admin cache --refresh`.

### 8.11 Open items carried in the doc

- Jira bot account (swap `JIRA_TOKEN`, no code change).
- Who NUSD wants as the JSM customer (`raiseOnBehalfOf`); the knob is not built.
- Close RC-40274.
