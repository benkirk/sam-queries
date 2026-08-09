# A notification framework in `sam.notify` — SMTP first, the ledger with it

> ☐ **Proposed. Nothing here is built.** This is the design for Sprint D of
> [`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md) § 2.1 / Phase 0.2 — the
> row that reads *"lift `EmailNotificationService` into `src/sam/notifications/`"*.
>
> Headline: it is **not** just a move plus a config wire-up, as that row assumes.
> A mailer inside `src/sam/` can mail real people from a dev container running a
> production snapshot, and neither existing consumer records what it sent. The
> lift is the easy half; the safety defaults (§ 3) and the ledger (§ 5) are the
> half that has to be got right.

**Handoff doc.** Written for a cold start. Every claim carries a `file:line` or a
measurement; re-verify rather than trust.

**Prior work:** PR #424 ([`XRAS_SPRINT_B.md`](XRAS_SPRINT_B.md) § *Notify, with
SMTP still deferred*, and
[`implemented/XRAS_SPRINT_B_FOLLOWUP.md`](implemented/XRAS_SPRINT_B_FOLLOWUP.md)
§ *The adjacent bug*). Sprint B deliberately shipped a record-only Notify button
and a *"not implemented"* dialog rather than `mailto:` or a rushed mailer. This
document is the follow-on it named.

**Naming deviation, recorded here rather than applied backwards:** the package is
`src/sam/notify/`, not the `src/sam/notifications/` that `XRAS_REIMPLEMENTATION.md`
names. `sam.notifications` collides confusingly with `cli.notifications`, the
thing being deleted. Older sprint docs are as-built records of shipped work and
are not rewritten to match.

---

## Why this is worth doing now

Three separate things converge on the same window, and it closes when PR #424
merges.

**1. The Notify button is a promise the code has already made.**
`src/webapp/dashboards/allocations/blueprint.py:1494-1532` resolves the
recipients, writes `xras_activation_event(event_type='notified',
notified_to=…)`, logs `'XRAS notify recorded (no mail sent)'`, and renders
`templates/dashboards/allocations/partials/xras_notify_not_implemented.html` —
whose own comment reads:

> ⚠️ THIS DIALOG IS THE FEATURE, not a placeholder to delete later. […] When SMTP
> lands, this template is replaced by a real send and **the schema does not
> change**.

That is a correct call for Sprint B and a bad place to launch from. XRAS projects
arrive `active = 0` and a human activates them; legacy SAM mails the PI. After
cutover nobody does, unless an operator remembers to copy addresses out of a
dialog.

**2. There is a working mailer, trapped in the CLI.**
`src/cli/notifications/email.py` (266 lines) is stdlib `smtplib` + Jinja2 with
zero Flask coupling — genuinely liftable. What makes it un-liftable *as-is*:

| | |
|---|---|
| `email.py:188-219` | drives a `rich.progress.Progress` bar against `self.ctx.console`, inside the send loop |
| `email.py:24-30` | config duck-typed off a CLI `Context`, duplicating `src/config.py:31-37` |
| `email.py:32-34` | templates on `Path(__file__).parent.parent / 'templates'` |
| `email.py:127,138` | a hardcoded `Bcc: benkirk@ucar.edu`, both branches |
| everywhere | no `logging`, no DB write, no state file — nothing is recorded |
| `email.py:141-146` | one connection per message, no timeout, broad `except Exception` folded into a `(bool, str)` tuple |

**3. The DBA window.** `xras_action_log` and `xras_activation_event` exist in dev
and CI only, from tracked self-retiring `initdb.d` scripts; the production
`CREATE TABLE`s are **an unfiled DBA request**. The prod writer holds
`SELECT/INSERT/UPDATE/DELETE` and no DDL
(`scripts/repair/RUNBOOK-missing-projects.md:36-38`), and `migrations/README.md`
records `sam` as Alembic-unmanaged. So every new SAM table costs external lead
time, and a *second* ticket costs another full round of it — the exact reasoning
that put `zz-91` on the same ticket as `zz-90` before the feature that used it
existed. Any table this work wants must be settled while that ticket is open.

And the bug those three make one problem rather than three:
`implemented/XRAS_SPRINT_B_FOLLOWUP.md:283-297` records that
`sam-admin project --upcoming-expirations --notify` **persists nothing at all**,
so every invocation inside the 32-day window re-mails the entire roster, admin
and lead of every matching project. It closes:

> Deliberately out of scope — a general notification ledger serving both would be
> designing for a second consumer nobody has specified […] But it is a real bug,
> and **if a notification ledger is ever wanted, *that* is the moment to fold both
> in.**

The second consumer now exists. This is that moment.

---

## Decisions

| | |
|---|---|
| **Tables** | Two. `notification_log` (live) and `notification_subscription` (DDL + ORM + tests, **dormant** — no UI, no enforcement; activated later with no DBA involvement). |
| **Send path** | Synchronous inline, short socket timeout, **outbox-ready**: rows are written `queued` and updated to `sent`/`failed`, so a drain can be added later with no DDL and no caller change. |
| **CLI** | Fully migrated. `src/cli/notifications/` deleted, templates moved into the library, the hardcoded `Bcc` becomes config, the 21 existing tests rewritten. |
| **Scope** | SMTP only. The `Transport` abstraction makes Slack a new module plus an address kind, not a refactor — but no Slack now. |
| **Visibility** | A **Notifications** card on Admin › Configuration, linking to a dedicated activity-log page. **No `sam-admin` equivalent** — deliberately, see § 8. |
| **Branch** | Stacks on `xras_reimplementation`; the new DDL joins PR #424's DBA ticket. |

---

## 1. Package layout — `src/sam/notify/`

Modelled on `src/sam/integration/awards/` and `src/sam/caching/`: an ABC plus a
registry plus per-backend modules, a pure re-export `__init__.py` with a sorted
`__all__`, module-level `logger = logging.getLogger(__name__)` and no handler
configuration inside `sam/`.

```
src/sam/notify/
    __init__.py       re-export + __all__
    base.py           Channel, Recipient, Message, RenderedMessage,
                      DeliveryResult, Transport (ABC), NotifyError/TransportError
    config.py         NotifyConfig — Flask-config-or-env
    kinds.py          NOTIFICATION_KINDS registry (the vocabulary)
    render.py         TemplateRenderer — Jinja2 env, facility resolution
    audience.py       Recipient builders from ORM objects
    ledger.py         notification_log writes + the suppression query
    registry.py       transport name → factory
    service.py        Notifier — the single public entry point
    transports/
        __init__.py
        smtp.py       SmtpTransport
        null.py       NullTransport    (records, never sends — the test default)
        console.py    ConsoleTransport (renders to a sink — dev / --dry-run)
    templates/
        expiration-UNIV.{txt,html}   expiration-WNA.{txt,html}
        xras_activation.{txt,html}   (new)
```

```python
class Channel(StrEnum):          EMAIL = 'email';  SLACK = 'slack'   # declared, no transport

@dataclass(frozen=True)
class Recipient:  address: str; name: str|None; role: str|None; channel: Channel = EMAIL

@dataclass(frozen=True)
class Message:
    kind: str                                  # a NOTIFICATION_KINDS key
    recipient: Recipient
    subject: str
    context: Mapping[str, Any]                 # splatted into the template, as today
    facility: str|None = None                  # template selection
    entity: tuple[str, int]|None = None        # ('project', 4711)
    projcode: str|None = None
    dedup_key: str|None = None
    requested_by: str = 'system'

@dataclass(frozen=True)
class RenderedMessage: subject; text; html|None; template_text; template_html|None

@dataclass(frozen=True)
class DeliveryResult:  ok: bool; status: str; detail: str|None; log_id: int|None

class Transport(ABC):
    name: ClassVar[str]; channel: ClassVar[Channel]
    @abstractmethod
    def deliver(self, message, rendered) -> None: ...    # raises TransportError
    def check(self) -> tuple[bool, str|None]:            # connectivity probe for the admin card
        return (True, None)
```

**`deliver()` raises rather than returning a bool.** The ledger owns status; a
transport's only job is "did it go". The current `(bool, error_str)` tuple is
what makes every caller re-invent status handling — `send_batch_notifications`
mutates an `'error'` key into the caller's own dicts to work around it
(`email.py:208,217`).

`Notifier` is the whole public surface:

```python
class Notifier:
    def __init__(self, *, config=None, transport=None, ledger_session_factory=None)
    def preview(self, message) -> RenderedMessage
    def send(self, message) -> DeliveryResult
    def send_many(self, messages, *, on_result=None) -> list[DeliveryResult]
```

`on_result` is the seam that keeps `rich` out of the library while the CLI still
gets its progress bar.

---

## 2. Config — `NotifyConfig`

Follows the established framework-agnostic seam at
`src/sam/caching/buckets.py:65-71` — `try: flask.current_app.config` /
`except RuntimeError: os.environ` — generalised to
`_config_str` / `_config_bool` / `_config_int`. This is the only pattern in
`sam/` that reads Flask config, and it exists for exactly this case: a core
library that must work identically under the CLI and the webapp.

`MAIL_*` already exists (`src/config.py:31-37`) and is already inherited into
`app.config` via `SAMWebappConfig(SAMConfig)` (`src/webapp/config.py:14`),
unused. New alongside it:

| var | default | meaning |
|---|---|---|
| `NOTIFY_ENABLED` | **`false`** | master switch. Fail-closed. |
| `NOTIFY_TRANSPORT` | `smtp` | `smtp` / `console` / `null` |
| `NOTIFY_REDIRECT_TO` | *(empty)* | when set, **every** message is re-addressed here |
| `NOTIFY_BCC` | *(empty)* | replaces the hardcoded `Bcc` |
| `MAIL_TIMEOUT` | `10` | socket timeout, seconds |

`MAIL_USERNAME`/`MAIL_PASSWORD` keep their current meaning (login only when both
are set).

---

## 3. Non-prod safety — the part most likely to go wrong

Standing a mailer up inside `src/sam/` means every dev container, CI worker and
laptop can mail **real users**, because they all run against an obfuscated copy
of production and obfuscation does not remove the mail relay. Three layers, all
fail-closed:

**1. `NOTIFY_ENABLED` defaults to `false`, everywhere.** Nothing sends unless a
deployment explicitly opts in. A missing config in production is "no mail,
visibly `suppressed` in the ledger" — noticed within a day and recoverable. The
inverse default makes the first `docker compose up` against a prod snapshot a
PII incident. `helm/values.yaml` sets `NOTIFY_ENABLED: "1"` explicitly, and
`ProductionConfig.validate()` emits a startup **warning** — the mechanism it
already uses at `src/webapp/config.py:307` — so the disabled state is never
silent.

**2. `NOTIFY_REDIRECT_TO` — the staging/dev mode.** Mail flows end to end and
nobody real is touched: the recipient is rewritten, the original preserved in
`notification_log.intended_recipient`, an `X-SAM-Original-To` header set, and a
banner line prepended to the body. Status is recorded as `redirected`, **not**
`sent`, so the ledger never claims a delivery that did not reach its subject.

**3. `TestingConfig` pins `NOTIFY_ENABLED=False` and `NOTIFY_TRANSPORT='null'`**,
on the same reasoning it already zeroes cache TTLs (`src/webapp/config.py:337`)
— a test tier that can reach shared state is a test tier that eventually does.

Plus the structural gate in § 10a: an autouse fixture that makes
`smtplib.SMTP`/`SMTP_SSL` raise, so **no test can open a socket regardless of
config**.

And `containers/sam-sql-dev/anonymize_sam_db.py` gains `purge_notification_log`
beside the existing `purge_xras_action_log` — the ledger holds addresses, and
the obfuscated dump is a committed public LFS blob.

---

## 4. Templates

Move `src/cli/templates/expiration*` → `src/sam/notify/templates/`.

**Delete the two symlinks** (`expiration.txt -> expiration-UNIV.txt`, same for
`.html`) and replace them with an explicit constant in `render.py`:

```python
DEFAULT_FACILITY_TEMPLATE = 'UNIV'      # what the old symlinks meant
```

Resolution becomes `{base}-{facility}` → `{base}-{DEFAULT_FACILITY_TEMPLATE}`.
Behaviour is unchanged — today an unmatched facility falls back to
`expiration.*`, which *is* the UNIV file — but the fallback becomes readable
rather than a filesystem trick, and it survives a wheel build.

Two latent packaging bugs this forces out, both invisible today because
everything runs from an editable install:

- `pyproject.toml:92-95` has no `[tool.setuptools.package-data]` and no
  `MANIFEST.in`, so `templates/` is not installed. Add
  `"sam.notify" = ["templates/*"]`.
- `jinja2` is **not a declared dependency** — it arrives transitively via
  `flask`. A `sam.notify` that imports it directly must declare it.

**Filters.** `src/sam/fmt.py:459-488` `register_jinja_filters(app)` writes
`app.jinja_env`, so a standalone `jinja2.Environment` gets none of
`fmt_number` / `fmt_date` / `fmt_size`. Refactor it to accept **any**
`Environment`, keeping a thin app-taking wrapper for the existing call site.
CLAUDE.md's "no raw `'{:,.0f}'.format()` / `.strftime()` in display code" rule
should hold in an email body too.

---

## 5. `notification_log`

`containers/sam-sql-dev/initdb.d/zz-92-notification_log.sql`, following
`zz-91-xras_activation_event.sql` exactly: `CREATE TABLE IF NOT EXISTS`,
self-retiring, `ENGINE=InnoDB DEFAULT CHARSET=utf8mb3`, and **no
`DEFAULT CURRENT_TIMESTAMP`** — the server resolves UTC while SAM is
naive-Mountain, and MySQL rounds fractional seconds rather than truncating.
Stamp `datetime.now()` from the app clock.

| column | type | notes |
|---|---|---|
| `notification_log_id` | `INT UNSIGNED` PK AI | |
| `kind` | `VARCHAR(32)` NOT NULL | a `NOTIFICATION_KINDS` key |
| `channel` | `VARCHAR(16)` NOT NULL | `email` (future `slack`) |
| `transport` | `VARCHAR(16)` NOT NULL | what actually handled it |
| `status` | `VARCHAR(16)` NOT NULL | `queued`/`sent`/`failed`/`suppressed`/`redirected` |
| `recipient` | `VARCHAR(255)` NOT NULL | the address actually used |
| `intended_recipient` | `VARCHAR(255)` | set only when redirected |
| `recipient_name` | `VARCHAR(255)` **utf8mb4** | human text |
| `recipient_role` | `VARCHAR(16)` | `lead`/`admin`/`user`/`operator` |
| `subject` | `VARCHAR(255)` **utf8mb4** | human text |
| `template` | `VARCHAR(64)` | the text template actually chosen |
| `entity_type` | `VARCHAR(32)` | `project`/`allocation`/`user`/NULL |
| `entity_id` | `INT` | **no FK**, deliberately |
| `projcode` | `VARCHAR(30)` **utf8mb3** | denormalized; charset matches `project.projcode` |
| `dedup_key` | `VARCHAR(128)` utf8mb3 | the suppression key |
| `error` | `TEXT` **utf8mb4** | defensively truncated in Python |
| `requested_by` | `VARCHAR(35)` NOT NULL | `users.username` width; `'cli'`/`'system'` when unattended |
| `creation_time` | `DATETIME` NOT NULL | |
| `sent_time` | `DATETIME` NULL | |

Indexes: `(dedup_key, creation_time)`, `(kind, creation_time)`,
`(status, creation_time)`, `(recipient, creation_time)`,
`(entity_type, entity_id)`, `(projcode, creation_time)`.

**The charset split is not cosmetic.** Only human-text columns are utf8mb4:
utf8mb3 under `STRICT_TRANS_TABLES` turns one emoji in a subject line into error
1366, and the ledger row is *lost* — the same failure `zz-90` records for
`raw_payload`. `projcode` must stay utf8mb3 because it is compared against
`project.projcode`; commit `5aef6bb` measured a utf8mb4 value there turning a
`const` index seek into a 4,650-row index scan, and called the split a cutover
precondition.

**Why a generic `entity_type`/`entity_id` and no FK.** A notification is about
whatever prompted it — a project today, an allocation or a user tomorrow, and
for an unmapped XRAS path nothing at all. A column per entity is a forest of
nullable FKs that grows with every new kind, and *each addition is a DBA ticket*
— precisely the cost this design exists to avoid. The trade is no referential
integrity, which is correct for an append-only historical record: a deleted
parent must not cascade the evidence away. `projcode` is denormalized beside it
because "did we mail anyone about SCSG0001" is the one query that matters, and
because the ledger has to stay readable after a project is renamed.

**Append-only, with exactly one permitted transition.** One row per delivery
*attempt*; a retry is a **new row sharing the `dedup_key`**, never an edit. The
sole exception is `queued → sent|failed` (plus `sent_time`, `error`), which is
that same row's own outcome rather than a state overwrite. This keeps
`xras_activation_event`'s discipline while giving the outbox its lifecycle — and
a process that dies between the two writes leaves the row `queued`, which is
what a future drain scans for and today reads as an honest "we do not know"
rather than a silent loss.

**Transaction discipline — the opposite of the route next to it.**
`blueprint.py:1466-1491` writes its activation event *inside*
`management_transaction`, on purpose, because a decision that did not take
effect must not survive. A ledger row is the inverse: **mail handed to a relay
cannot be un-sent by a rollback**, so it must survive one. The ledger therefore
commits on its own short-lived session, mirroring
`src/webapp/api/xras/replay.py`. The two disciplines sit two screens apart and
must be documented in both docstrings — a reader who has just read one will
expect the other answer.

**Suppression** — the fix for the re-email bug. Each kind builds a `dedup_key`:

```
expiration        expiration:{projcode}:{latest_end_date:%Y-%m-%d}:{recipient}
xras activation   xras_activation:{projcode}:{xras_action_log_id}:{recipient}
```

The second one *is* the existing derive rule (`"marked notified" iff
latest('notified') > latest_action`) expressed as a key — the anti-spam
mechanism the XRAS card already got right, generalised to every kind.
`ledger.already_sent(session, dedup_key, since=…)` matches
`status IN ('sent','queued','redirected')`; `--force` overrides.
**Dry-run writes no row** — a preview is not an attempt, and a stray
`suppressed` row would poison the dedup query.

---

## 6. `notification_subscription` (dormant)

`zz-93-notification_subscription.sql`. Model, factory and tests ship; nothing
reads it yet.

| column | type | notes |
|---|---|---|
| `notification_subscription_id` | `INT UNSIGNED` PK AI | |
| `user_id` | `INT` NOT NULL FK → `users.user_id` | **signed**, or MySQL rejects the FK |
| `kind` | `VARCHAR(32)` NOT NULL | a `NOTIFICATION_KINDS` key, or `'*'` |
| `channel` | `VARCHAR(16)` NOT NULL | `email`/`slack` |
| `address` | `VARCHAR(255)` NULL | NULL = "use `user.primary_email`"; set = an override |
| `scope_type` | `VARCHAR(16)` NOT NULL | `global`/`project`/`facility` |
| `scope_id` | `INT` NULL | NULL when global |
| `enabled` | `TINYINT(1)` NOT NULL DEFAULT 1 | a row with 0 is an opt-**out** |
| `created_by` | `VARCHAR(35)` NOT NULL | |
| `creation_time` / `modified_time` | `DATETIME` NOT NULL / NULL | app clock |

`UNIQUE (user_id, kind, channel, scope_type, scope_id)`.

**"No row" is not a policy — the kind is.** `NOTIFICATION_KINDS` declares
`default_subscribed: bool` per kind. Expiration notices are `True`: they are
transactional, a PI must be told their allocation is expiring, and a missing row
must never silence that. Operational feeds like `xras.action_failed` are `False`
— opt-in. A row with `enabled=0` is the opt-out; `enabled=1` on a default-off
kind is the opt-in.

**That is why the dormant shape needs no second DBA ticket when it activates**:
adding a subscribable kind is a Python constant, and both polarities are already
expressible in the columns that exist.

Unlike the ledger this table is deliberately **mutable** — a preference is
current state, not history — and its audit trail is the existing before-flush
listener in `src/webapp/audit/events.py`, which already logs every UPDATE with
its changes.

---

## 7. Wiring the two consumers

### The XRAS Notify button

Today's one-click POST records intent. A real send is irreversible, so it becomes
two steps — the same reasoning that already puts an `hx-confirm` on
`xras_activate`:

- `GET /xras_notify_form/<project_id>` → a **preview modal**: the recipients, the
  rendered subject and text body, and a Send button. Better than a confirm
  dialog, because it also answers "what will they actually receive".
- `POST /xras_notify/<project_id>` → `Notifier.send_many(...)`, then, **inside**
  `management_transaction`, the existing
  `XrasActivationEvent('notified', notified_to=<the addresses that succeeded>)`.
  Send first, record what happened second.

No path may 500 — `Notifier.send` never raises on transport failure, only on
programmer error:

| outcome | response |
|---|---|
| all sent | `htmx_success` + `refreshXrasTab`, naming who was mailed |
| partial | success fragment naming the failures; the event records only the successes |
| all failed, or notify disabled | the **existing** `xras_notify_not_implemented.html`, renamed `xras_notify_manual_fallback.html` and reworded. It already does the right thing — say plainly nothing was sent, hand over the addresses. No activation event is written. |

The XRAS schema does not change, exactly as that template promised:
`xras_activation_event.notified_to` stays the operator-facing summary, the derive
rule is untouched, and `notification_log` is the delivery evidence beside it.

### The CLI expiration notices

`ProjectExpirationCommand._send_notifications`
(`src/cli/project/commands.py:284-412`) keeps building the audience and payload —
that is expiration domain logic — but emits `Message` objects and calls
`Notifier.send_many(messages, on_result=advance_progress)`. `rich` stays in
`src/cli/project/display.py`; dry-run calls `Notifier.preview()` and keeps
`display_notification_preview`. Add `--force` to override suppression, and fix
the unguarded `project.lead.primary_email` at `commands.py:392` while there — it
`AttributeError`s on a project with no lead, two lines below a `project_lead`
that *is* guarded.

### The two recipient resolvers

They stay two **audiences**, which is correct — roster+admin+lead for
expirations, lead+admin only for the XRAS handoff
(`get_xras_pending_recipients`, `src/sam/queries/xras_activation.py:294-338`,
deliberately kept off the `VIEW_XRAS` render path so contact PII never reaches a
viewer who is not entitled to it). What they share is a `to_recipients(...)`
adapter in `sam/notify/audience.py`. Their name-formatting divergence
(`display_name` vs `full_name or username`) is **left alone**: unifying it
changes strings the XRAS card's tests pin, for no benefit here.

---

## 8. The admin surface

A framework nobody can see the output of is a framework nobody trusts. Two
surfaces, following the **Rate limiting** precedent exactly — a summary tile on
the Configuration tab whose header carries a `Details »` link to a dedicated
page.

### The Notifications card

A `col-12 col-xl-6` tile in
`src/webapp/templates/dashboards/admin/fragments/configuration_card.html`,
between Caching and Rate limiting, fed by a `notifications` block added to
`gather_runtime_state()` in `src/webapp/utils/config_inspect.py` — the sole place
that reads `app.config`/`os.environ` and where secrets are masked, so
`MAIL_PASSWORD` never reaches the template.

```
📧 Notifications                                        Details »
   Enabled              No            ← the fail-closed default, stated
   Transport            smtp
   Relay                ndir.ucar.edu:25   (TLS off)
   From                 sam-admin@ucar.edu
   Redirecting to       you@ucar.edu  ← shown ONLY when NOTIFY_REDIRECT_TO is set,
                                        in a warning colour. A staging box quietly
                                        swallowing mail is the failure mode this
                                        line exists to prevent.
   ──────────────────────────────────
   Sent (24h)           38
   Failed (24h)          0
   Suppressed (24h)     11
   Queued (stuck)        0   ← non-zero means a process died mid-send
```

Counts come from one grouped query, `summarize_notifications(session, …)`, in a
new `src/sam/queries/notifications.py`. The card shows **no addresses**.

### The activity log page

`GET /admin/htmx/notifications` rendering
`templates/dashboards/admin/notifications.html`, with an HTMX fragment at
`GET /admin/htmx/notifications/log`. The structure is lifted from the XRAS
action-log page, which is the same problem already solved well:

- **Facet chips with self-exclusion** on `status`, `kind` and `channel`, via the
  shared `facet_row` macro (`dashboards/fragments/facet_chips.html`) and the same
  discipline as `xras_fragment` (`blueprint.py:1306-1340`): each dimension's
  rollup omits its *own* filter, so the chips stay switchers rather than dead
  ends. Scoping a dimension by itself drives every unselected value to zero the
  moment one is picked. Served by the `(kind, …)` / `(status, …)` indexes.
- **Sortable, paginated table** — time, kind, status, channel, recipient,
  subject, project, who asked. Free-text on recipient/projcode, plus a date
  window defaulting to 30 days as the XRAS page does.
- **Detail modal per row** — full subject, the template actually chosen (which
  makes the facility fallback auditable), `dedup_key`, `intended_recipient` when
  redirected, and the full `error` on a failure. Rendered bodies are **not**
  stored, so there is nothing to leak beyond the columns.
- **An activity chart** — a `StackedSeriesChart` subclass
  (`NotificationActivityChart`) over sent / failed / suppressed / redirected per
  day, above the table. Full chart contract: `cache_name`, `LAYOUTS = profile(…)`
  for desktop/tablet/mobile, a `chart_view` binding **appended** to
  `charts/__init__.py` (that call order is the admin Caching card's row order —
  never reorder), a case in `tests/unit/chart_samples.py`,
  `layout=read_layout(), theme=read_theme()` at the call site, and a
  `#sam/row/status/<value>` drill so clicking a band filters the table beneath.

### Permissions — one tier apart, deliberately

| surface | gate | why |
|---|---|---|
| Configuration card | `VIEW_SYSTEM_CONFIG` | counts and config only; no addresses |
| `/admin/htmx/notifications` | `SYSTEM_ADMIN` | every row names a real person's email |

This mirrors the split the XRAS page already makes (`VIEW_XRAS` for the log,
`MANAGE_XRAS` for the raw-payload panel) and the one `get_xras_pending_recipients`
makes in the query layer: contact PII is fetched only when the viewer is
entitled to it, at the **route** level, so a view-source cannot reveal what the
page chose not to draw.

### No CLI equivalent

Explicitly out of scope, and a deliberate divergence from the XRAS precedent
where `sam-admin xras` and the web page share a query layer *so the two cannot
drift*. `src/sam/queries/notifications.py` is still built as a shared query layer
— the webapp needs it and it keeps the door open — but no `sam-admin` command
consumes it.

---

## 9. Deployment plumbing

`helm/` has **zero** `MAIL_*` today. `helm/values.yaml`'s `webapp.env` block
gains `MAIL_SERVER`, `MAIL_PORT`, `MAIL_DEFAULT_FROM`, `NOTIFY_ENABLED`,
`NOTIFY_TRANSPORT` and `MAIL_TIMEOUT`; `MAIL_PASSWORD` needs an ExternalSecret
entry only if the relay turns out to require auth, which port 25 on
`ndir.ucar.edu` suggests it does not.

`src/webapp/logging_config.py:68` wires `('job_history', 'fs_scans')` as extra
logger roots because they inherit the root logger the app never configures. Add
`'sam'` — otherwise `sam.notify` emits nothing and a send is unobservable from
inside the app, which is the same silence that comment was written about.

---

## 10. Tests

New `tests/unit/test_notify_*.py` replace
`tests/unit/test_email_notifications.py` and `test_notification_enhancements.py`
(21 tests). The mocked-`smtplib` assertions port over largely unchanged; the four
`MagicMock` tests that re-implement grace-period/role/facility logic *instead of
calling it* are rewritten to exercise the real builders, since today the payload
builder at `commands.py:284-412` is effectively untested against the real code
path.

- `test_notify_render.py` — facility resolution + `DEFAULT_FACILITY_TEMPLATE`,
  text-only fallback, `sam.fmt` filters present in the notify env
- `test_notify_smtp_transport.py` — TLS, auth, timeout, `TransportError` mapping,
  multipart vs plain, `NOTIFY_BCC`
- `test_notify_service.py` — the guard matrix (disabled → `suppressed`,
  redirect → `redirected` + `intended_recipient`), the `queued → sent|failed`
  lifecycle, the `on_result` callback, dry-run writing no row
- `test_notify_config.py` — the Flask-config-or-env seam under both contexts
- `test_notification_log.py` / `test_notification_subscription.py` — `create()`
  vocabulary validation, the dedup query
- `test_notifications_queries.py` — `summarize_notifications`, including the
  self-exclusion property; the failure it prevents is a chip strip that reads
  all-zeros the moment one filter is picked
- `tests/factories/notify.py` — `make_notification_log`,
  `make_notification_subscription`, delegating to the models' own `create()`
  (`tests/factories/xras.py:103-119` is the pattern, including its xdist hazard
  note about deriving synthetic keys from DB-assigned PKs rather than a
  process-local counter)
- route tests for the changed `xras_notify*` endpoints and the two admin
  surfaces — auth / validation / render smoke, per the house convention that
  happy-path writes are covered at the model layer. ⚠️ Including the **negative**
  permission case: `VIEW_SYSTEM_CONFIG` alone gets the card and is **403'd** off
  the log page, because that boundary is the only thing keeping recipient
  addresses off a lower tier.
- `tests/unit/chart_samples.py` gains a `NotificationActivityChart` case (a gate
  requires one); it is then fingerprinted at every layout × theme automatically
- `tests/integration/test_schema_validation.py` picks both tables up
  automatically once they are exported from `src/sam/__init__.py`

**Structural gates**, in the repo's habit of making a silent failure loud:

- **(a) no socket, ever.** An autouse fixture in `tests/conftest.py` replaces
  `smtplib.SMTP`/`SMTP_SSL` with a raiser. A test that reaches a real relay
  fails with a sentence saying so, rather than mailing someone.
- **(b) kinds ↔ templates are bijective.** Every `NOTIFICATION_KINDS` entry
  resolves a text template for every `FacilityName`, and every file under
  `notify/templates/` is reachable from some kind — catching a kind with no
  template *and* an orphaned template.
- **(c) route-map parity** — `tests/unit/test_route_map_parity.py` regenerated
  with `ROUTE_MAP_REGEN=1` in the same commit as the new routes.
- **(d) chart fingerprints** — `CHART_FINGERPRINT_REGEN=1` in the commit that
  adds the chart and **only** there. A desktop-light delta anywhere else is a bug.

⚠️ Exporting the two models from `src/sam/__init__.py` **auto-registers
Flask-Admin views** under "Everything". `tests/unit/test_admin_defaults.py`
spot-checks rather than pinning the full set, so it should not break — but the
two views will exist, and `notification_log` holds addresses while `/database`
is kill-switched off only in production.

---

## Commit series

Each commit individually green.

| # | commit | contents |
|---|---|---|
| 1 | `feat(notify): channel-agnostic framework` | base types, config, renderer, transports, registry. No callers, no DB. |
| 2 | `feat(notify): move the expiration templates into the library` | delete the symlinks, `DEFAULT_FACILITY_TEMPLATE`, `package-data` + `jinja2`, `register_jinja_filters(env)` |
| 3 | `feat(db): notification_log + notification_subscription` | `zz-92`/`zz-93`, ORM models, `sam/__init__.py` export, schema validation, factories, `anonymize_sam_db.py` purge rule |
| 4 | `feat(notify): the ledger` | `queued→sent` lifecycle, suppression, the guard matrix |
| 5 | `refactor(cli): expiration notices run on sam.notify` | delete `src/cli/notifications/`, rewrite the 21 tests, `--force`, fix `project_lead_email` |
| 6 | `feat(webapp): the XRAS Notify button sends mail` | preview modal, real send, `xras_notify_manual_fallback.html`, route-map snapshot |
| 7 | `feat(admin): Notifications card on the Configuration tab` | `sam/queries/notifications.py`, the `config_inspect` block, the tile, the permission split |
| 8 | `feat(admin): the notification activity log` | page, facet chips, table, detail modal, `NotificationActivityChart`, route-map + fingerprint snapshots |
| 9 | `feat(deploy): MAIL_*/NOTIFY_* in helm, .env.example, logger roots` | |
| 10 | `docs: Sprint D as built` | this doc flipped to ✅, `XRAS_REIMPLEMENTATION.md` § Sprint D, `XRAS_CUTOVER_RUNBOOK.md:230`, `XRAS_SPRINT_B_FOLLOWUP.md` § *The adjacent bug* (now fixed), `src/webapp/README.md`, `docs/TESTING.md` counts, and a CLAUDE.md § *Notifications* |

---

## Verification

```bash
# 1. Pick up the two new tables (make docker-down has NO -v — this dance is required)
docker compose --profile test down -v && make docker-build && make docker-up

# 2. Schema first — the ORM↔MySQL drift gate
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest tests/integration/test_schema_validation.py -v

# 3. The notify tier, then the full suite
pytest tests/unit/test_notify_*.py tests/unit/test_notification_*.py -v
pytest                                  # ~90 s under xdist

# 4. CLI end to end on the console transport — no socket, real rendering
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=console \
  sam-admin project --upcoming-expirations --notify --dry-run --verbose
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=console \
  sam-admin project --upcoming-expirations --notify        # writes ledger rows
mysql -u root -h 127.0.0.1 -proot sam \
  -e "SELECT kind,status,transport,recipient,dedup_key FROM notification_log"
# re-run the same command: every row should now be skipped by suppression

# 5. Webapp — the XRAS Notify path
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=console NOTIFY_REDIRECT_TO=you@ucar.edu \
  docker compose up webdev --watch     # → http://localhost:5050
#   Allocations → XRAS → pending card → Notify: the preview modal renders the body;
#   Send records notification_log + xras_activation_event and the card reads "notified".
#   Then with NOTIFY_ENABLED=0: the manual-fallback dialog, no event written, no 500.

# 6. The admin surface
#   Admin → Configuration → the Notifications card carries the counts from steps 4-5
#   and the "Redirecting to" line in warning colour.
#   Details » → the activity log: chart bands agree with the table, every facet chip
#   switches (never all-zeros), a row modal names the template actually used.
#   As a user with VIEW_SYSTEM_CONFIG but not SYSTEM_ADMIN: card renders, page 403s.
```

**Handoff items that leave the repo:**

- **Add both new tables to PR #424's DBA ticket.** That ticket is a transcription
  of the `zz-9*` files; `zz-92`/`zz-93` join `zz-90`/`zz-91`. Filing separately
  costs another round of external lead time — the mistake `zz-91`'s header
  exists to prevent.
- **Staging needs all four run by hand once** —
  `infrastructure/scripts/init-rds.sh` restores the raw `.xz` with no initdb hook.

---

## Risks and open questions

1. ⚠️ **Can the k8s pods reach `ndir.ucar.edu:25` at all?** The CLI mailer runs
   from a workstation; nothing has ever sent mail from the cluster, and nothing
   in `helm/` or `docs/` records an egress rule for it. If it is blocked, that is
   a launch blocker for the webapp half — the CLI half is unaffected. This is the
   one item with an external dependency other than the DBA ticket, so check it
   early rather than at commit 6.
2. **Is `sam-admin@ucar.edu` a real, SPF-authorized sender for that relay?** A
   pod mailing with an unauthorized envelope-from gets rejected or
   spam-foldered, and spam-foldering is invisible to SMTP-level success — the
   ledger would read `sent`.
3. **Fail-closed means someone must set `NOTIFY_ENABLED=1` at launch.** Mitigated
   by the startup warning, but it is a real footgun. The alternative footgun is
   mailing production users from a laptop.
4. **The CLI's observable behaviour changes.** Suppression will skip recipients
   already mailed inside the window. That is the bug fix, but it is a change;
   `--force` is the escape hatch.
5. **Two clicks instead of one on XRAS Notify.** Recommended, because the act
   becomes irreversible — but it is a UX change to a surface that has already
   been reviewed.
6. **The activity chart is the only genuinely new UI surface**, and it carries
   the chart contract's full overhead: a sample case, fingerprints at three
   layouts × two themes, a cache row in the admin Caching card, and the
   `chart_view` ordering rule. If the schedule tightens, the log page works
   without it — table plus facet chips is still a visual log, and the chart is
   the cleanest thing to drop.
7. **Nothing here changes what XRAS itself receives.** These are NCAR-originated
   notices only; the broker contract is untouched.
