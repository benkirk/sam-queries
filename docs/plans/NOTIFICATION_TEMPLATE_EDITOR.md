# Operator-editable notification templates + shared-layout refactor

**Status: future enhancement, not scheduled.** Captured 2026-08-28 to mature the
idea and record the constraints before anyone re-derives them. Numbers are
measured against the dev clone on that date. Two intertwined wants live here on
purpose — see *Why these are one change*.

## The want

Email notification templates (expiration notices, the XRAS handoff notices, the
task summary) ship as Jinja2 files **inside the `sam.notify` Python package**
(`src/sam/notify/templates/`). Editing one is a source change + image rebuild +
redeploy. That is fine in early dev and too heavy for production operations: an
operator should be able to fix wording without a release.

Alongside that, the HTML templates carry real CSS/HTML technical debt — every
one repeats the same `<head>`/`<style>`/scaffold — and the two problems are best
solved together.

## Current state (measured 2026-08-28)

Templates are one `.txt` + one `.html` per variant, 8 logical templates / 16
files. `TemplateRenderer` (`src/sam/notify/render.py`) is a standalone Jinja2
`Environment` + `FileSystemLoader` with `sam.fmt` filters registered and
autoescape on the HTML variant only. Resolution is `{base}-{facility}` →
`{base}-UNIV` → `{base}`, selected by which `.txt` exists (`resolve()` — text
selects the variant, HTML follows it; the WARNING there explains why the two
halves must not split). Only `expiration` has facility variants (`-UNIV`,
`-WNA`). No template is sourced from anywhere but the filesystem; the DB
`notification_log.template` column only *records* which file was used
(`src/sam/notify/models.py`).

**The duplication:** all 8 HTML templates repeat the same
`<!DOCTYPE>`/`<head>`/`<meta>`/`<style>`/`<body>` scaffold and the same
`body` / `h3` / `.project-info` / `.footer` CSS; all 14 files (HTML + text)
repeat the footer sign-off line verbatim. The only real HTML variation is the
`.project-info` accent color (`#d9534f` for expiration, `#5cb85c` for
renewal/update) plus a few kind-specific blocks (`.grace-period`,
`table.allocations`, the `xras_update` footer-link tweak).

---

## Part A — shared-layout refactor (the CSS debt)

⚠️ **Email CSS is not web CSS.** The obvious "factor it out" move — a `<link>`
to a shared stylesheet — does not work in email: Outlook and much of the
mobile/webmail field strip `<head>`/external styles. Two viable shapes:

1. **Jinja base layout with `{% extends %}` (recommended).** Add
   `src/sam/notify/templates/_email_base.html` holding the scaffold, the shared
   `<style>`, and the footer, exposing `{% block extra_styles %}` and
   `{% block content %}`. Each kind template becomes
   `{% extends "_email_base.html" %}` + its content block; the accent color and
   kind-specific rules go in `{% block extra_styles %}`. Keeps the current
   `<head><style>` delivery (no deliverability regression) and collapses ~40
   lines of boilerplate per file. The `.txt` files stay plain text; a one-line
   `{% include "_email_footer.txt" %}` can DRY their sign-off.
2. **Build-time CSS inliner (premailer / juice) — deferred.** The
   most-robust-email path (inlined `style=""` attributes), but it adds a
   dependency and a render step. Note it as a later option, not the first move.

`FileSystemLoader` already supports `extends`/`include`, so Part A stands alone
with no renderer change beyond authoring the base. **Gate:** a golden-render test
(render each kind against a sample context, diff against a committed snapshot) so
the refactor is provably byte-stable before it lands.

---

## Part B — operator editing (DB-backed, sandboxed)

Design decisions: **in-place editor, DB-backed** (package files stay the
baseline); **sandboxed Jinja**. Three layers; build the bottom two and their
tests before any UI.

### Layer 1 — the override store

New model `NotificationTemplateOverride` in a new
`src/sam/notify/template_store.py`, mirroring the pure-SQLAlchemy
`NotificationLog` in `models.py` (no jinja2/transport imports — respect the
`sam/notify/__init__.py` eager-import trap in CLAUDE.md). Keyed on `stem` +
`format` (→ template name `{stem}.{format}`), unique together; `body` Text,
`modified_by`, `modified_time` (app clock, naive-Mountain, same convention as
`NotificationLog.creation_time`). Add `SessionMixin` and `update()` /
`create()` per CLAUDE.md §7. **"Reset to default" = delete the row** — the
package file is the permanent baseline; an override is purely additive. Register
in `src/sam/__init__.py`.

⚠️ **SAM has no migrations** (the database is the schema source of truth; Alembic
covers only `system_status`). A new table follows the established dev pattern —
a `zz-NN-*.sql` init script under `containers/sam-sql-dev/initdb.d/`
(cf. `zz-90-xras_action_log.sql`, `zz-91-xras_activation_event.sql`,
`zz-92-notification_log.sql`) — plus a **production DBA grant/DDL step**, the
process in `docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md`. It is not an
Alembic migration.

### Layer 2 — renderer sources overrides + sandbox

Surgical changes in `render.py` — every path funnels through
`self.env.get_template()`, so the loader and environment swap change behavior
everywhere with no logic rewrite:

- **Loader** → `jinja2.ChoiceLoader([DBTemplateLoader(session_factory),
  FileSystemLoader(TEMPLATE_DIR)])`. Override wins, package file falls back, so an
  un-overridden template renders exactly as today. `DBTemplateLoader` is a
  `jinja2.BaseLoader` whose `get_source()` queries the override table and returns
  `(body, name, uptodate)`; the `uptodate` callable compares `modified_time` so a
  long-lived `Environment` drops its compiled copy when a row changes.
- **Environment** → `jinja2.sandbox.SandboxedEnvironment` (a subclass;
  `FileSystemLoader`, `select_autoescape`, and `fmt.register_jinja_filters`
  compose unchanged). It blocks the attribute/dunder access SSTI needs; registered
  `fmt` filters run as trusted Python and are unaffected.
- **`__init__`** gains an optional `session_factory` — absent → filesystem-only,
  keeping existing tests working. `Notifier` (`service.py`) threads in its
  ledger's factory.

⚠️ **Multi-replica staleness is a non-issue** because the DB is the single
source: every pod, and the task CronJob (which builds a fresh renderer per run),
reads current rows. The only in-memory concern is one long-lived `Environment`'s
compiled cache, handled by `uptodate`.

### Layer 3 — the admin UI ("the simple text editor")

Per CLAUDE.md §9, this is **tier 3 (`HtmxFormHandler`)** — it edits blob content,
not a CRUD-quintet entity.

- **Gate** `@require_permission(Permission.SYSTEM_ADMIN)` (not `_any_facility`).
  Templates are system-wide, not facility-scopable; this matches the precedent
  for the mutating half of the notifications surface (`htmx_clear_cache`, the
  notifications-log module). A dedicated page off the Notifications tile — **not**
  the read-only Configuration card, whose contract forbids state-mutating
  controls.
- **List** the shipped stems × `{txt, html}` with *default (package)* vs
  *customized (override)* and who/when. **Edit** view = two `textarea_field`
  bodies + **Preview** + **Save** + **Reset to default**. New form schema in
  `src/sam/schemas/forms/` (exported); `clean()` compiles each body via the
  sandboxed env → `FormError` on `TemplateSyntaxError`; `perform()` runs inside
  `management_transaction` (audit by construction).
- **Preview** renders against a per-kind `sample_context(kind)` helper — new,
  import-cheap, in the notify package; the shapes already exist in
  `tests/unit/test_expiration_message_builder.py` and
  `tests/unit/test_xras_notices_builder.py`. Reuse `Notifier.preview()` (writes no
  ledger row). ⚠️ **Document the limit:** undefined names render empty (the
  templates rely on it as a guard — see `src/sam/queries/xras_notices.py`), so
  preview catches syntax and filter errors, not a mistyped variable name.
- **Widget:** plain `textarea_field` first. CodeMirror highlighting is an
  optional later add — one sha384-pinned file via `src/webapp/vendor_assets.py`
  plus the re-hash test. Small and bounded; not required.

---

## Why these are one change (the coupling)

Operators should edit **content, not chrome.** After Part A the shared
scaffold/CSS lives in `_email_base.html`; the editor exposes only the per-kind
**child** templates, so a bad edit cannot break the frame of every email. The
underscore-prefixed base stays developer-owned code, excluded from the editable
set — so the CSS refactor *shrinks Part B's blast radius*, which is why they
belong together.

Mechanics that already line up: the `ChoiceLoader` resolves `_email_base.html`
from the package `FileSystemLoader` even when the child is a DB override, and the
sandbox permits `extends`/`include`.

⚠️ **Open question to settle at build time:** does the DB store the whole child
file (an operator could alter the `{% extends %}` line — the sandbox limits the
damage) or only the content block (safer, but complicates the txt/html variant
symmetry)? Storing the content block is the safer default.

## Security posture

1. **`SandboxedEnvironment`** — the line between "operators edit
   prose + placeholders" and "operators run arbitrary Python on the mail host."
2. **`SYSTEM_ADMIN` gate** — fails closed, held only by full-override users today.
3. **Compile-on-save** rejects malformed templates before any real send.
4. **Audit** — `management_transaction` plus the row's own
   `modified_by` / `modified_time`; reset is a delete and the shipped file is
   always recoverable.
5. Recall the framework blast radius (CLAUDE.md § Notifications: the relay reaches
   any `128.117.0.0/16` address) — the reason preview and compile-gating are
   mandatory, not optional.

⚠️ **The one gate to prove before Layer 2 merges:** every shipped template must
render identically under `SandboxedEnvironment`. The `fmt` filters and the
templates' attribute access should be fine, but verify it rather than assume it.

## Phasing

0. **Part A** layout refactor + golden-render test — self-contained, useful alone.
1. **Layers 1–2** + fallback / SSTI / shipped-parity tests — the risky core;
   overrides are settable via Flask-Admin in the meantime.
2. **Layer 3** admin UI.
3. **Optional** — CodeMirror, a diff-vs-default view, premailer inlining, and
   body-edit history if more than "who last touched it" is wanted.

## Files an implementation will touch

- `src/sam/notify/templates/_email_base.html` (new) and the 8 `*.html` (refactor
  to `{% extends %}`)
- `src/sam/notify/template_store.py` (new model) and `src/sam/__init__.py`
  (register)
- `containers/sam-sql-dev/initdb.d/zz-93-notification_template_override.sql` (new
  dev DDL) + a prod DBA step
- `src/sam/notify/render.py` (`ChoiceLoader` + `SandboxedEnvironment` +
  `session_factory`) and `src/sam/notify/service.py` (thread the factory)
- a `sample_context(kind)` helper in `src/sam/notify/`
- `src/sam/schemas/forms/` (new schema) and the admin routes/templates under
  `src/webapp/dashboards/admin/`, plus a nav entry in `src/webapp/utils/nav.py`
