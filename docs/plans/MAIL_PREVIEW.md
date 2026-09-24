# Mail preview sweep: preview every webapp email before it is sent

## Handoff prompt (paste into a fresh session)

```
Implement PR 1 of the mail-preview sweep in /Users/benkirk/codes/project_samuel/devel
(SAM). The spec is docs/plans/MAIL_PREVIEW.md; if that file is missing, copy it from
~/.claude/plans/while-i-watch-ci-sequential-pine.md into docs/plans/MAIL_PREVIEW.md
first and commit it with PR 1. Read it in full before starting. It records the
decisions and is the spec.

Goal of PR 1 (foundation):
- Notifier.preview_delivery(), which mirrors the real send: redirect plus banner,
  family addressing, NOTIFY_BCC through Transport.envelope_copies, and
  "already sent on <date>" through NotificationLedger.last_sent_many. It never
  writes, and a read_only ledger means it can never send.
- get_notifier(read_only=...) always loads template overrides and addressing
  rows. This fixes the Project Notify preview, which skips operator overrides
  today.
- One shared macro, dashboards/fragments/email_preview.html, with a recipient
  picker for batches.
- Move the XRAS Notify and Edit Project -> Notify previews onto it.

Fixed decisions (do not revisit):
- Checkbox-driven sends get a "Preview email" control beside the checkbox. It
  renders from the current form values, with placeholders, and saving and
  sending stay as they are today. That work is PR 2; do not start it in this
  session.
- Two PRs: foundation (this one), then the new sites (invite, resend, roster x2,
  reject, digest, renew/extend).
- Previews are read-only: no ledger row, no session writes, no real tokens.

Workflow:
- Branch from origin/staging; the PR targets staging.
- Follow CLAUDE.md: section 9 (form handlers), section 12 (comment budget),
  American spelling, no skip-ci tokens in commit messages or PR bodies. Load the
  wire-dashboard-feature skill before touching templates, because of the modal
  traps in PR #464.
- Run the gates listed in the spec, regenerate the route map with
  ROUTE_MAP_REGEN=1, and run the full suite on MySQL 3307 and on Postgres 5434.
- Smoke-test on local webdev with NOTIFY_TRANSPORT=console. Use a scratchpad
  compose override; do not edit .env. Test with notify disabled and with
  NOTIFY_REDIRECT_TO set, and check that a template override now shows in the
  Project Notify preview. Watch only syncs files edited after webdev starts, so
  touch the changed files once it is up.
- Then pause and summarize for Ben before opening the PR.
```

> Handoff for a fresh session. First step: copy this file to
> `docs/plans/MAIL_PREVIEW.md` in the repo so the plan travels with the branch.
> Branch each PR from `origin/staging` (it includes #608, the invitation links)
> and target `staging`. Line numbers below were read at `30780072`; re-verify
> them, because they drift.

## Context

Recent PRs added several operator-triggered emails: the invitation links
(#608), the renewal and extension notices (#581, #586), and the account-request
rejection notice and digest. Only two of the webapp's 11 send sites show the
operator the mail before it leaves: XRAS Notify and Edit Project → Notify.
Those two:
- duplicate their preview markup;
- preview only the first recipient;
- don't show what the real send does (the redirect banner, cc, bcc,
  `NOTIFY_BCC`, from, reply-to);
- don't say *when* a copy was already sent.

On top of that, Project Notify renders with `get_notifier(ledger=False)`, which
silently **skips operator template overrides and addressing rows**. Its preview
can therefore differ from the mail that is actually sent.

**What this delivers:** one read-only preview path that mirrors delivery, one
shared preview macro, and a "Preview email" control at every operator send
point.

**Decisions (Ben, 2026-09-24):**
- Checkbox-driven sends get a **Preview button before save**. It renders the
  mail from the current form values, with placeholders for what only exists
  after the save. The save-and-send flow stays unchanged.
- **Two PRs.** PR 1 is the foundation plus moving the two existing previews onto
  it. PR 2 adds previews at the new sites.

**Out of scope:** the anonymous `/register` verify mail (the visitor sends it,
not an operator), and tasks and the CLI (they already have `--dry-run`
previews through `Notifier.preview`).

## Send-site census (webapp)

| # | Site | Route (send) | Kind | Trigger | Preview today |
|---|---|---|---|---|---|
| a | Invite one person | `project_invites.htmx_invite_user` | `account_invite` | `send_invite` checkbox, on by default | no |
| b | Resend invite | `project_invites.htmx_resend_invite` | `account_invite` | paper-plane button with `hx-confirm` | no |
| c | Roster paste ×2 | `project_invites.htmx_roster_paste`, `admin_dashboard.htmx_admin_event_roster` (shared `RosterHandler`) | `account_invite`, batch | `send_invite` checkbox, on by default | no |
| d | Reject | `admin_dashboard.account_request_reject` | `account_rejected` | `notify` checkbox, off by default | no |
| e | Digest | `admin_dashboard.account_requests_digest` | `account_queue_summary` | "Send to NUSD" button with `hx-confirm` | no |
| f | Renew / Extend | `admin_dashboard.htmx_renew_allocations` / `htmx_extend_allocations` → `_maybe_notify_renewal` (`projects_routes.py` ~1729) | `project_renewal`, batch | `notify_leads` checkbox, on by default, plus a comment | no |
| g | Project Notify | `admin_dashboard.htmx_notify_project` | `project_activation` / `project_adjustment`, batch | modal + per-row select | yes (first message only; no overrides) |
| h | XRAS Notify | `allocations_dashboard.xras_notify` | `xras_*`, batch | modal | yes (first message only) |

Sites a, c, d and f build their mail in `after_commit` from data the save
creates: the request id the invite token is signed with, `closed_at`, and the
allocations the renewal `touched`. Their previews therefore need placeholders or
a read-only plan of what the save will do. Sites b and e can be previewed
exactly as they will be sent.

---

## PR 1: foundation

### 1.1 `sam.notify`: a read-only preview that mirrors delivery

**`src/sam/notify/base.py`** (imported eagerly with no dependencies, which the
import-graph gate requires):
- Add frozen dataclasses `PreviewRecipient(address, name, role, last_sent)` and
  `DeliveryPreview(mode, transport, recipients, selected, outgoing, sender,
  reply_to, cc, bcc, rendered, error)`, where `mode` is
  `'disabled' | 'redirected' | 'live'`.
- Add a default method `Transport.envelope_copies(message) -> (cc, bcc)` that
  returns `message.copies()`.
- Export both dataclasses from the eager `base` import list in
  `sam/notify/__init__.py`.

**`NOTIFY_BCC` belongs in the transport.** `SmtpTransport.envelope_copies`
(`transports/smtp.py` ~142-149) appends `config.bcc_addresses` and
de-duplicates, and `envelope_recipients` is built from its result. It still
applies to a **redirected** message, as today. The console and null transports
add nothing. The preview asks the transport, so preview and send can't disagree.

**`src/sam/notify/config.py`**: add `NotifyConfig.sender_for(message)`.
`SmtpTransport.sender_address` delegates to it.

**`src/sam/notify/service.py`**:
- Pull `_outgoing(msg)` (redirect, then addressing) and `_render_outgoing(msg)`
  (render, then the redirect banner) out of `_deliver_one` (~306-319), and have
  `_deliver_one` use them.
- Add `preview_delivery(messages, *, selected=None) -> DeliveryPreview`:
  - validate the kinds;
  - call `ledger.last_sent_many` once;
  - render **only the selected** message;
  - catch render errors into `error`;
  - read cc/bcc from `transport.envelope_copies(outgoing)`;
  - never call `_record`.
- Leave `preview()` as it is; the CLI and tasks use it.

**`src/sam/notify/ledger.py`**:
- Add `last_sent_many(keys) -> {key: datetime}`: `max(creation_time)` grouped by
  `dedup_key`, sharing `_suppression_conditions` and following the chunked,
  fail-open structure of `already_sent_many`. It uses the
  `(dedup_key, creation_time)` index.
- Add `NotificationLedger(read_only=True)`. In that mode `record()` raises, so
  `_deliver_one` refuses to deliver, and `resolve()` does nothing. **A preview
  notifier can't send.**

### 1.2 Webapp notifier

In `src/webapp/utils/notify.py`, change `get_notifier(ledger=True)` to
`get_notifier(read_only=False)`. It always builds a session-backed ledger, so
template overrides and operator addressing rows always load. That fixes the
Project Notify mismatch (`projects_routes.py` ~2225).

Add `notify_config()` for the callers that only read `.config`
(`projects_routes.py` ~2190 and ~2250; `account_requests_routes.py` ~249).

Add a new `src/webapp/utils/email_preview.py`, the single webapp entry point:
- `email_preview_context(messages, *, id_prefix, pane_id, picker_url=None,
  picker_method='post', picker_include=None, notes=(), empty=None)`;
- `render_email_preview(messages, **kw)`;
- `render_preview_info(text, level='info')`.

It reads `preview_recipient` from `request.values` and always answers **200**
with an info or error panel, because htmx won't swap an error response.

### 1.3 One macro and one pane

New `templates/dashboards/fragments/email_preview.html`.

`email_preview(p, id_prefix, pane_id, picker_url, picker_method, picker_include,
notes)` renders, in order:
1. the mode banner (disabled, redirected with the intended address, or a
   console/null transport note);
2. the placeholder `notes`;
3. the "Already sent on {{ … | fmt_date }}" line for the selected recipient;
4. a **recipient picker** when there is more than one recipient: a
   `<select name="preview_recipient">` wired with `hx-{method}`, change, and a
   target of `#pane_id`;
5. the envelope box: From, To (with the intended address when redirected),
   Reply-To, Cc, Bcc, Subject and the template names;
6. HTML | Plain-text tabs, with ids derived from `id_prefix`, rendered as
   `<iframe class="notify-preview-frame" sandbox srcdoc="{{ html }}">` (a bare
   `sandbox`, no tokens), or a single `<pre>` when there is no HTML;
7. the error panel.

Move the iframe reasoning comments from `xras_notify_form.html` into this file,
compressed. The macro must **never emit a `<form>`** (the pane lives inside the
send form) and **never emit `data-bs-toggle`**.

The same file also holds `preview_button(url, pane_id, label='Preview email')`:
a `btn-link` with `hx-post` targeting the pane. Placed inside the form, it sends
the form's current values.

`templates/dashboards/fragments/email_preview_pane.html` only calls the macro;
every preview route renders it.

`checkbox_field` (`form_fields.html` ~371) renders `{{ caller() }}` inline after
the label when it is used with `{% call %}`, so a preview button can sit beside
a checkbox without duplicating its markup.

CSS: move `.notify-preview-text` and `.notify-preview-frame` from `admin.css`
(~123-138; loaded only on admin pages) to the global `components.css`, and add
`.notify-preview-frame-tall` for the template editor. That drops the inline
`style="height…"` attributes. The rules use only tokens, so the
`test_css_tokens` counts don't change.

### 1.4 Move the two existing previews onto it

**XRAS Notify:**
- Add `GET /allocations/xras_notify_preview/<project_id>?action_id=&preview_recipient=`,
  endpoint `allocations_dashboard.xras_notify_preview`, with the form's guard
  (`@login_required @require_permission(MANAGE_XRAS)`).
- A shared `_xras_preview_ctx(project, action)` builds messages with
  `_xras_messages` and calls `email_preview_context`.
- `xras_notify_form` uses it. `already_notified` becomes
  `[r for r in p.recipients if r.last_sent]`: one ledger query instead of one per
  recipient.
- In the template, replace lines ~45-69 and ~89-182 with
  `<div id="xrasPreviewPane">{% include pane %}</div>`. Keep the inactive
  alert, the force checkbox and the footer.
- Keep `id_prefix='xrasPreview'` so the existing tab-id tests still pass.

**Project Notify:** `htmx_notify_project_preview` switches to
`email_preview_context(..., id_prefix='notifyPreview',
pane_id='notifyPreviewPane', picker_method='get',
picker_include='#notifyOperatorComment')`. The self-refreshing wrapper's
`hx-include` gains `#notifyPreviewRecipient`, so editing the comment keeps the
chosen recipient.

### 1.5 PR 1 tests

- **`test_notify_service.py`**, `TestPreviewDelivery`:
  - redirect: rewrites To, sets the intended address, puts the banner in text
    and html, and empties the message's own cc/bcc, while `NOTIFY_BCC` stays
    under smtp;
  - family env addressing plus operator rows;
  - disabled mode;
  - `selected` picks by address and falls back to the first;
  - a render error is captured, not raised;
  - an unknown kind raises;
  - `record` is never called (spy);
  - `last_sent` is populated.
- **`TestPreviewAndSendAgree`**: a capturing transport's `send()` sees the same
  cc, bcc and rendered output as `preview_delivery()`, and
  `SmtpTransport.envelope_recipients(outgoing) == [to, *p.cc, *p.bcc]`.
- **`test_notify_ledger.py`**:
  - `set(last_sent_many(k)) == already_sent_many(k)` across `SUPPRESSION_CASES`
    (extend `TestTheSingleAndBatchFormsAgree`);
  - the returned time is the latest one;
  - it fails open;
  - empty input opens no session;
  - a read-only ledger's `record` raises.
- **`test_notify_smtp_transport.py`**: `envelope_copies` includes the global Bcc
  and de-duplicates it.
- **Webapp**:
  - `get_notifier(read_only=True).send()` returns `failed` and the transport is
    never called;
  - the renderer loader and the addressing store have a session factory (the
    regression test for the override bug).
- **New `test_email_preview_macro.py`** (render with a hand-built
  `DeliveryPreview`):
  - bare `sandbox`, no live `<style>`, no `<form`, no `data-bs-toggle="modal"`;
  - tab ids follow the prefix, and there are no tabs without HTML;
  - the picker appears only with more than one recipient;
  - each mode shows its banner;
  - the Bcc line and the last-sent line show.
- **Update** `test_xras_notify.py` (also patch
  `webapp.utils.email_preview.get_notifier`) and `test_notify_project_routes.py`,
  and add a picker round trip.
- **Gates**: CSP lint, modal shell contract, static assets, CSS tokens, the
  notify import graph, docs, and route-map regen (`ROUTE_MAP_REGEN=1`).
- **CLAUDE.md**: add one "Preview" row to the Notifications table, and remove
  the `ledger=False` wording.

---

## PR 2: the new preview sites

**Rule:** each preview calls **the same message-building function the send
calls**; where it needs something only the save creates, it takes a parameter.

- Previews are **POST** requests carrying the form's values (free text stays
  out of URLs and logs; CSRF comes from the body's `hx-headers`). Resend is the
  exception: a GET with ids only.
- Each preview is guarded by **the same decorator as its send route**.
- The pane always answers 200. Validation problems show as an info panel.
- **Never write:**
  - no session add, and no mutation of a persistent row (autoflush plus the
    audit `before_flush` listener in `webapp/audit/events.py` would record it);
  - never sign a real token;
  - never reconcile;
  - no savepoint dry run (the audit log records it, and it uses up ids).

### Refactors that keep preview and send on one path

1. **`sam/manage/account_requests.py`**: a new batch read,
   `invite_outcomes(session, project_id, emails) -> {email: (outcome, username)}`.
   `invite_user` calls a single-email wrapper of it, so its classification and
   the preview's can't drift.
2. **`webapp/register/invite_mail.py`**:
   - pull `invite_messages(rows, *, sent_at, requested_by, link_for)` out of
     `send_invite_links`, which then passes the real token link;
   - add `preview_invite_rows(...)`, which returns **transient**
     `AccountRequest` objects: columns only, never added to the session;
   - add `INVITE_LINK_PLACEHOLDER`, e.g.
     `…/register/invite/PREVIEW-link-is-created-when-sent`, built without
     `url_for` because the blueprint may be unmounted. It resolves as invalid
     if anyone clicks it.
3. **`sam/queries/account_notices.py`**: `build_rejection_message` gains
   `reason=` and `closed_at=` overrides that default to the row's values.
4. **`admin/account_requests_routes.py`**: pull `_rejection_message(row, reason,
   closed_at)` and `_digest_message(rows, now)` out of the reject and digest send
   paths.
5. **`admin/projects_routes.py`**: pull `_renewal_messages(root, *, action,
   new_end, active_at, touched, comment)` out of `_maybe_notify_renewal`, and
   `_renew_form_input()` / `_extend_form_input()` out of the handlers'
   `form_input`.
6. **`sam/manage/renew.py` and `extend.py`**:
   - read-only step generators (`_renew_steps`, `_extend_steps`) that hold the
     anchor, overlap and descendant walk; the write functions loop over them;
   - `plan_renew_allocations(...)` and `plan_extend_allocations(...)` return a
     `PlannedAllocation(account, amount, start_date, end_date)` exposing the
     attributes `_resource_rows` and `build_renewal_messages` read. Renew uses
     the same scaling and `round_to_sig_figs` rule as the write.
   - This is the riskiest refactor. **Do it last in PR 2, and split it into a
     PR 3 if it grows.**

### Per site

| Site | Preview route (endpoint) | Builds from | Pane / control |
|---|---|---|---|
| a Invite | `POST /project-invitations/<projcode>/invite-preview` (`project_invites.htmx_invite_preview`, `_GUARD`) | `InviteUserForm`, then `invite_outcomes`: added / duplicate / ambiguous give an info panel ("no email"); queued goes to `invite_messages(preview_invite_rows(...), link_for=placeholder)` | `{% call checkbox_field('send_invite') %}{{ preview_button }}` plus `#invitePreviewPane`. Note: link created on Invite (valid N days). |
| b Resend | `GET /project-invitations/<projcode>/requests/<id>/invite-preview` (`project_invites.htmx_resend_invite_preview`) | the real row, through `invite_messages([row], link_for=placeholder)` | **Two steps, replacing `hx-confirm`.** The tab's paper-plane button opens `#invitationModal` (card-level `data-bs-toggle`) holding the new `resend_invite_preview_htmx.html`: pane, Cancel, and "Send link" (`hx-post` to the send, `hx-swap="none"`). The send response adds `closeActiveModal`. |
| c Roster ×2 | `POST …/events/<code>/roster-preview` on both blueprints (`project_invites.htmx_roster_preview`, `_EVENT_GUARD`; `admin_dashboard.htmx_admin_event_roster_preview`, `_ROSTER_GUARD` plus `_roster_target`), both calling a shared `roster_preview()` in `event_lifecycle.py` | `parse_roster`, then `invite_outcomes`. Header: "30 people: 24 get a link, 5 already known (enrolled, no email), 1 already waiting". Picker over the queued people. Admin copy: an info panel when `ACCOUNT_INVITATIONS_ENABLED` is off. | `RosterHandler.preview_endpoint`; the button beside `send_invite` in `roster_form_htmx.html`; `#rosterPreviewPane`. |
| d Reject | `POST /admin/account-requests/<id>/reject-preview` (`MANAGE_ACCOUNT_REQUESTS`) | `AccountRequestReasonForm`, then `_rejection_message(row, reason=…, closed_at=now)` (no reason: "type a reason to preview") | Button beside `notify` in `account_request_reason_form_htmx.html`; `#rejectPreviewPane`. |
| e Digest | `GET /admin/account-requests/digest-preview` | `queue_requests` **without reconciling**, then `_digest_message`. Note: "Send reconciles first: up to N ready requests drop out" (`queue_counts()['ready']`). Suppression gives "already sent today at …". | **Two steps.** The card button opens `#auditDetailsModal` holding a new `account_requests_digest_preview_htmx.html` with Cancel and Send; the digest response adds `closeActiveModal`. |
| f Renew / Extend | `POST /admin/htmx/{renew,extend}-allocations-preview/<projcode>` (`require_project_facility_permission(EDIT_ALLOCATIONS)`) | `_renew_form_input()`, then the schema, then `plan_*_allocations`, then `_renewal_messages(...)`. An empty plan gives "nothing would change; no email". Picker over leads and admins. The dedup key uses the real `new_end`. | Button in the `notify_leads` row; `#renewPreviewPane` / `#extendPreviewPane` after the comment box. |

Give each site its own `id_prefix`. The Renew and Extend modals can both be in
the DOM at once.

**Modal traps (PR #464):** in-modal controls only `hx-target` a pane. The only
new `data-bs-toggle` attributes are on the card and tab openers (b and e), where
the modal is still closed; those hosts are already pinned in
`HTMX_FRAGMENT_SHELL_DEPS`. Check the pins with
`test_modal_shell_contract.py`.

### PR 2 tests

- **Manage:**
  - `TestPlanMatchesTouched`: the plan equals the real write's `touched` (inside
    the test savepoint) for inheriting, standalone and child-only anchors,
    overlap with and without `replace_existing`, and `scale != 1`, plus Extend's
    open-ended and already-long cases;
  - `invite_outcomes` agrees with `invite_user`.
- **Builders:**
  - the `build_rejection_message` overrides work, and the defaults leave the
    message unchanged;
  - `invite_messages` produces the message `send_invite_links` sends
    (`test_account_invite_links.py` stays green).
- **Routes:** each preview answers 200 for the authorised role and is refused
  like its send route; every info branch is covered.
- **Previews write nothing:**
  - `db.session.new` and `dirty` are empty after the call;
  - monkeypatch `tokens.invite_token` to raise (no token is signed);
  - monkeypatch `reconcile_account_requests` to raise (digest);
  - the `notification_log` row count is unchanged;
  - the reject preview leaves `closed_reason` unchanged.
- **Triggers:** the Resend and digest success responses carry `closeActiveModal`.
- **Gates:** as PR 1, plus `test_action_cells_nowrap` and
  `test_collapse_trigger_rows`. Regenerate the route map for the eight new
  endpoints.

---

## Verification (each PR)

1. Run `pytest` on MySQL 3307 and `SAM_TEST_DB_URL=postgresql+psycopg2://sam_test:sam_test@127.0.0.1:5434/sam pytest`.
2. Smoke on webdev (`docker compose up webdev --watch`). After starting, touch
   the changed files, because watch syncs only later edits; also
   `docker exec samuel-cache redis-cli -n 0 FLUSHDB`, and restart for the CSS
   move. Check every site in desktop light, desktop dark and mobile.
   Load the `wire-dashboard-feature` skill first.
   - Open the modal and fill the form; **Preview email** renders in the pane
     and the modal stays open.
   - The HTML and Text tabs both work; the iframe is white in dark mode.
   - The picker switches recipient (roster, Renew, XRAS, Project Notify).
   - Editing a field and previewing again shows the change.
   - The real Save still works: same toast, and no 400 (a 400 would mean a
     CSRF problem).
   - The console is clean: no `htmx:targetError`, no CSP violations.
3. Run with `NOTIFY_ENABLED=0` (disabled banner), then with `NOTIFY_REDIRECT_TO`
   set (redirect banner, banner text in the body, intended address shown,
   `NOTIFY_BCC` listed under smtp).
4. Add a template override under Admin → Notifications → Templates; the Project
   Notify preview now shows it (the PR 1 bug fix).
5. Resend and digest: Send closes the modal and shows a toast. A second digest
   preview on the same day shows "already sent".

## Risks

| Risk | Mitigation |
|---|---|
| A preview that sends | The read-only ledger's `record` raises, so `_deliver_one` refuses to deliver. Tested. |
| A preview that writes | Transient rows, no mutation of persistent rows, no reconcile, no savepoint dry run. Every preview test asserts nothing is new or dirty. |
| A real token leaking | Previews never call `invite_token`; the placeholder link resolves as invalid. |
| Preview and send drifting apart | Shared builders and step generators, plus the agreement tests (plan vs `touched`, preview vs a capturing transport, `last_sent_many` vs `already_sent_many`). |
| Import graph | Keep `*_notices` modules unexported from `sam.queries`. New notify types go in `base.py`. `invite_mail` stays lazily imported in `event_lifecycle`. |
| Remote images | CSP `img-src 'self' data:` blocks them in the iframe. That's the case today too; note it in the macro, and don't widen the CSP. |
| Stale previews | No caching on any preview route. |
