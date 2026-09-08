# Operator-editable notification templates + shared-layout refactor

**Status: implemented 2026-09-07** (branch `customizable_notifications`, one
PR against `staging`, nine commits). The design was captured 2026-08-28; this
is the record of what landed and where it deviated. Production DDL is a
separate hand-applied step (below).

## The want

Email templates (expiration notices, the XRAS handoff notices, the task
summary) ship as Jinja2 files inside the `sam.notify` package
(`src/sam/notify/templates/`). Editing one was a source change, an image
rebuild and a deploy. An operator can now fix wording from Admin →
Notifications → Templates without a release. Alongside, the eight HTML
templates repeated the same `<head>/<style>` scaffold; that debt is gone.

## What landed, in commit order

1. **`sam/notify/samples.py`** — `sample_context(kind, facility)` returns
   fixed literals shaped exactly like each builder's real context;
   `preview_context()` adds the four renderer-injected keys; `palette(kind)`
   flattens that into `{name, shape, example, note}` rows using
   `VARIABLE_NOTES`. Parity with the builders is pinned beside each
   builder's tests. This one module is both the preview input and the
   editor's "known variables" table, so the two cannot drift.
2. **Golden renders** — `tests/unit/test_notify_golden_renders.py` renders
   all 16 files for a lead and a member into
   `tests/unit/snapshots/notify_renders/`. Text compares byte-exact, HTML
   whitespace-normalized. `NOTIFY_RENDER_REGEN=1` regenerates.
3. **Part A** — `_email_base.html` holds the scaffold, the shared CSS and
   the footer; the eight HTML files `{% extends %}` it and fill
   `extra_styles`, `content`, and `footer_extra` or `footer`. The `.txt`
   files are untouched (their `{%- -%}` whitespace control makes an include
   risky for no gain). `shipped_template_names()` excludes underscore
   partials; `TestNoOrphans` uses it and a new test requires every partial
   to be referenced.
4. **Sandbox** — `ImmutableSandboxedEnvironment`; the golden renders passed
   unchanged, which is the parity proof. `render_source(name, source,
   context)` renders an unsaved body through an overlay environment (not
   `from_string`, which would lose the name-keyed autoescape);
   `undeclared_names()` lints a body against the palette.
5. **Templates tab** — `?tab=log|templates` on the existing page. The pane
   is a picker plus an editor card: source, variables table, "preview as"
   role, Preview pane below (text in `<pre>`, HTML in a sandboxed `srcdoc`
   iframe; the page CSP already allows inline styles). Errors answer inside
   the pane at 200. The editor deep-links to the log by kind.
6. **Store** — `NotificationTemplateOverride`
   (`src/sam/notify/template_store.py`), one row per template file name,
   `body` utf8mb4, app-clock `modified_time`. DDL:
   `scripts/create_notification_template_override.sql`.
7. **Loader** — `OverrideLoader` in `render.py`, see below. `Notifier`
   derives the session factory from its ledger, so no call site changed.
8. **Write path** — `_SaveTemplateHandler` (`HtmxFormHandler`) with
   `NotificationTemplateForm`; `clean()` renders the body against the
   sample context and refuses anything that does not render; Reset deletes
   the row.
9. This record, the CLAUDE.md row, and the stale docstring citations.

## Decisions that differ from the original design

| Original | Landed | Why |
|---|---|---|
| `containers/sam-sql-dev/initdb.d/zz-93-*.sql` | `scripts/create_notification_template_override.sql`, hand-applied; test-DB blob regenerated | `initdb.d/` was retired; `hpc-writer` now holds CREATE on prod (`DBA_PRIVILEGE_REQUEST.md`), so a new table is an operator step, not a ticket. |
| `ChoiceLoader([DB, FS])` with an `uptodate` re-check | One loader, one `SELECT` per renderer, no re-check | Every consumer builds a fresh `Notifier` per request (`webapp/utils/notify.py`) or per task/CLI run, so there is no long-lived cache to invalidate. A re-check would cost a query per `get_template()`, four per message. |
| Store the content block only | Store the whole child file | Keeps txt/html symmetric and the source honest; the sandbox bounds the damage of a broken `{% extends %}` line, and the base partial is never in the editable set. |
| Key on `(stem, format)` | Key on `name` (`expiration-WNA.txt`) | The loader, the ledger's `template` column and the UI all speak in file names. |
| Editor / Preview sub-tabs; log at the bottom | Stacked editor then preview; a deep link to the log | Source and result stay visible together; one log fragment on the page. |
| A `nav.py` entry | None | The page is reached from the Configuration tile's `Details »`, as before. |

## Traps worth keeping

- **A sandbox refusal is a runtime error.** `compile_source` accepts
  `{{ ''.__class__.__mro__ }}`; only rendering raises `SecurityError`. The
  save gate therefore renders against the sample context.
- **`from_string` loses autoescape.** `select_autoescape` keys on the
  template *name*; a nameless string renders an `.html` body unescaped.
  Previews go through `env.overlay(loader=ChoiceLoader([DictLoader(...),
  loader]))`, which also keeps the real cache untouched.
- **`hx-confirm` is a Bootstrap modal here**, not `window.confirm`
  (`#samConfirmModal`). A scripted smoke must click its Confirm button.
- **Route writes commit.** The one happy-path save test uses
  `task_summary.txt`, which nothing else renders through a real session,
  and deletes the row in `finally`.
- **Blob regeneration never dumps port 3306.** The dev database may hold
  real data. The recipe: recreate `mysql-test` from a clean volume (it
  restores the committed blob), apply the DDL there, dump from
  `mysql-test` with the Makefile's flags, then diff the `CREATE TABLE`
  list against the committed blob through `git lfs smudge` (a bare `git
  show` yields the LFS pointer).
- **Undefined names render empty by design** (`sam/queries/xras_notices.py`
  relies on it), so the editor warns on an unknown top-level name and
  cannot see a misspelled attribute inside a loop.

## Production rollout

1. Apply, as `hpc-writer` on the UCAR VPN (the operator's step, before the
   deploy), `scripts/create_notification_template_override.sql`,
   `scripts/create_notification_addressing.sql` and
   `scripts/alter_notification_log_copies.sql`. Until then the loader and
   the addressing store each log one warning per `Notifier` and fall back
   to the shipped files and the env defaults; nothing is withheld. The
   ledger write, however, names the `copies` column, so the ALTER must land
   before the deploy.
2. Deploy. Both tables are empty, so every mail renders and is addressed
   exactly as before.
3. The ledger's `template` column still records the file name only.
   Whether an override was live at send time is answerable from
   `notification_template_override.modified_time` against
   `notification_log.creation_time`.

## Follow-up: "Preview for" a real project (same PR)

The editor's "Preview as" role select gained a "Preview for" project picker
(`fk_search_field` on a SYSTEM_ADMIN typeahead that reuses the
parent-project search and results template). Once a project is picked, the
audience fragment (`GET .../templates/<name>/recipients`) replaces the role
select with the real people the builders would address, and the pane
re-renders the real message: `sam/queries/notification_previews.py`
builds the expiration 4-tuples from `project.accounts` (no per-project
window query exists, and the window ones drop inactive or open-ended
projects) and calls `build_xras_messages(..., kind=)` — a new keyword that
forces the template's own kind; increments are taken from the latest
action only when its service maps to that kind, so a forced supplement
never shows an adjustment's amounts as "added". Real and possibly empty,
never fixture numbers: the pane explains when nothing would be sent.

Mechanics worth keeping: `fk-picker.js` fires `fk:selected` /
`fk:cleared` (never `change`), so the audience div listens for those; its
response carries `HX-Trigger: reloadTemplatePreview`, which the pane
listens for, so the re-render always follows the recipient select. The
picker lives outside the save form because its search box posts as `q`.

## Follow-up: operator-added cc/bcc (same PR)

Copies per message family were a chart edit (`NOTIFY_<FAMILY>_{CC,BCC}` in
`helm/values.yaml`, only `xras` set). The Addressing tab lets an operator
add to them without a deploy.

- **One table, `notification_addressing`**, one address per row:
  `scope`, `field` (cc|bcc), `address`, who/when,
  `UNIQUE(scope, field, address)`. The scope vocabulary is
  `addressing_scopes(family)` in `kinds.py`: the family key, each kind
  key, and `{kind}-{facility}` for facility-aware kinds. That last stem is
  the same one the template resolver tries, and it is what makes a
  WNA-only expiration copy one row.
- **Additive.** Effective cc = (builder-set value or env family default)
  plus the rows for the family, the kind, and the facility variant, in that
  order, deduped case-insensitively; bcc likewise. From/Reply-To stay
  env-only. Deployment defaults are shown on the card but are not rows,
  so they cannot be removed from the UI; an operator row is removed by
  deleting it.
- **Read once per `Notifier`** (`AddressingStore`, `sam/notify/addressing.py`)
  through the ledger's session factory, mirroring `OverrideLoader`. No
  factory means no query. Any failure logs one warning and adds nothing:
  the ledger, not this read, decides fail-closed for a dead database.
- **`Message.copies()`** now owns the redirect rule (empty when
  `intended_recipient` is set); the SMTP and console transports and the
  ledger all read it. **`notification_log.copies`** records what left as
  `cc:a@x,b@y;bcc:c@z` (NULL when nothing did, which includes every
  redirected row); the detail modal shows it.
- **Families are a registry**: `NotificationFamily(key, label,
  about_project)` in `kinds.py`. `about_project` is what the template
  editor's "Preview for" reads; the tab uses the labels.
- The template editor's preview pane prints the effective
  From/Reply-To/Cc/Bcc for the template's kind, so an operator can see a
  row take effect without sending.

Traps: the add form's macros must be imported `with context`, or the
inline errors render empty at 200 and look like a dead button. The card
reloads on `reloadAddressingCard`, which both the add (through
`handle_htmx_form_post`) and the delete response fire.

## Deferred

CodeMirror highlighting (one sha384-pinned asset via
`src/webapp/vendor_assets.py`), a diff-vs-default view, premailer inlining,
and edit history beyond "who last touched it".
