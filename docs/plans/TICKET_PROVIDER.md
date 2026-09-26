# Ticketing from SAM: the ithelp Jira API and a thin ticket-provider layer

Status: design record, 2026-09-26. Phase 0 shipped with this document. Phases
1-3 are designed, not built. Phase 2 is blocked on the service-account question.

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

- Keep a `TicketProvider` ABC (about 40 lines) with **exactly one
  implementation**. It earns its keep for the test fake and because the callers
  never import `requests`, not because of Cloud.
- Do **not** pre-build a Cloud provider. JSM Cloud exposes the same
  `/rest/servicedeskapi/request` shape, so the create path barely moves. The
  delta is auth (Basic `email:api_token` instead of Bearer), the base URL, and
  the v2 search endpoint (`/rest/api/2/search` is retired on Cloud for
  `/rest/api/2/search/jql`). That is a config flag plus one method, about 30 lines.
- Do **not** add a generic `external_ticket` table, and store neither the URL nor
  a provider name. One subject type exists; the issue key survives a migration
  verbatim; the URL is `JIRA_BASE_URL/browse/<key>` at render time, so the
  migration touches zero rows.
- Do **not** add `notification_log.external_ref`. Revisit when a second kind
  needs an external reference.

## 5. Phases

| Phase | What | Schema | CronJob env | Status |
|---|---|---|---|---|
| 0 | `[SAM-AR-<id>]` token in the mail subject | none | none | **shipped with this doc** |
| 1 | read-only client; learn the key hourly; `RC-40274`-style link on the Accounts card | ALTER, four columns | read keys + token | designed |
| 2 | JSM create replacing mail behind `TICKET_PROVIDER=jira` | none | none | blocked: service account |
| 3 | status sync; optional internal note on fulfillment | none | none | marginal |

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
- Ask for a Jira service account for RC (decides phase 2).
- Ask NUSD who the customer should be on an API-filed request (section 5, phase 2).
- Close RC-40274 from the UI.
