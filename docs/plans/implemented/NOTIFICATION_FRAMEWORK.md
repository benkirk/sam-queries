# A notification framework in `sam.notify` — SMTP first, the ledger with it

> ✅ **Built.** Ten commits on `smtp_notify`, stacked on `integration`. See
> § *As built* at the end for what changed on the way, including **four
> claims in this document that measurement disproved**. This is the design
> for Sprint D of
> [`XRAS_REIMPLEMENTATION.md`](../../xras/incoming/XRAS_REIMPLEMENTATION.md) § 2.1 / Phase 0.2 — the
> row that reads *"lift `EmailNotificationService` into `src/sam/notifications/`"*.
>
> Headline: it is **not** just a move plus a config wire-up, as that row assumes.
> A mailer inside `src/sam/` can mail real people from a dev container running a
> production snapshot, and neither existing consumer records what it sent. The
> lift is the easy half; the safety defaults (§ 3) and the ledger (§ 5) are the
> half that has to be got right.

**Handoff doc.** Written for a cold start. Every claim carries a `file:line` or a
measurement; re-verify rather than trust.

**Revised 2026-08-09** on `smtp_notify`, off `integration` after PR #424 merged:
the relay unknowns are now **measured** (§ 9); a dormant subscription table and
an activity chart are **cut** (§ 6, § 8); § 5 gained a suppression case that
would have shipped as a deadlock.

**Prior work:** PR #424 ([`XRAS_SPRINT_B.md`](../../xras/incoming/implemented/XRAS_SPRINT_B.md) § *Notify, with
SMTP still deferred*, and
[`XRAS_SPRINT_B_FOLLOWUP.md`](XRAS_SPRINT_B_FOLLOWUP.md)
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
| **Tables** | **One.** `notification_log`. A subscription/preferences table was designed and **cut** — see § 6 for why, and for the one idea worth keeping when it is eventually specified. |
| **Send path** | Synchronous inline, one connection per batch, short socket timeout, **outbox-ready**: rows are written `queued` and updated to `sent`/`failed`, so a drain can be added later with no DDL and no caller change. |
| **CLI** | Fully migrated. `src/cli/notifications/` deleted, templates moved into the library, the hardcoded `Bcc` becomes an envelope Bcc from config, the 22 existing tests rewritten. |
| **Scope** | SMTP only. The `Transport` abstraction makes Slack a new module plus an address kind, not a refactor — but no Slack now. |
| **Visibility** | A **Notifications** card on Admin › Configuration, linking to a dedicated activity-log page: facet chips, a sortable table, a detail modal. **No chart** (§ 8) and **no `sam-admin` equivalent** (§ 8), both deliberately. |
| **Branch** | `smtp_notify`, stacked on `integration` after PR #424 squash-merged; the new DDL joins PR #424's DBA ticket. |

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
    def open(self) -> None: ...                          # a batch opens once
    def close(self) -> None: ...
    def check(self) -> tuple[bool, str|None]:            # connectivity probe for the admin card
        return (True, None)
```

**`deliver()` raises rather than returning a bool.** The ledger owns status; a
transport's only job is "did it go". The current `(bool, error_str)` tuple is
what makes every caller re-invent status handling — `send_batch_notifications`
mutates an `'error'` key into the caller's own dicts to work around it
(`email.py:208,217`).

**`open()`/`close()` exist so the rewrite does not reproduce the flaw it is
fixing.** `email.py:141-146` opens a fresh `smtplib.SMTP` *inside* the send loop
— one TCP connect plus one STARTTLS handshake per recipient — and a per-message
`deliver()` is the same shape. `send_many` wraps the batch: `open()` once,
`deliver()` per message, `close()` in a `finally`. `send` is `send_many` of one;
`null`/`console` inherit the no-op defaults.

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
| `NOTIFY_BCC` | *(empty)* | an **envelope** Bcc; replaces the hardcoded address |
| `MAIL_TIMEOUT` | `10` | socket timeout, seconds |
| `NOTIFY_QUEUED_STALE_SECONDS` | `300` | how long a `queued` row blocks its own retry — see § 5 |

**`MAIL_USE_TLS` flips to `true`** — it defaults `false` (`src/config.py:34`)
and nothing ever exercised it; § 9 measured STARTTLS working on the one relay
both consumers use. `MAIL_USERNAME`/`MAIL_PASSWORD` keep their meaning (login
only when both are set) but are inert there — no `AUTH` is advertised.

**There are two sources of truth for `MAIL_*` today, not one.**
`src/cli/core/context.py:29-35` re-reads the same six env vars off `os.getenv`
with the same defaults, bypassing `SAMConfig` entirely — which is why the CLI
has never honoured a `SAMConfig` change. `NotifyConfig` replaces both; deleting
the `Context` copy is part of commit 5.

---

## 3. Non-prod safety — the part most likely to go wrong

Standing a mailer up inside `src/sam/` means every dev container, CI worker and
laptop can mail **real users**, because they all run against an obfuscated copy
of production and obfuscation does not remove the mail relay.

⚠️ **And the blast radius is larger than "real users".** The § 9 probe offered
the relay an external recipient and got `250 2.1.5 Ok` — `ndir` relays for the
whole `128.117.0.0/16`, every VPN workstation as well as every pod. A mailer
that defaults on does not merely mis-mail a PI whose address survived
obfuscation; it can mail **anywhere on the internet**, over an envelope-from
that SPF-passes as `ucar.edu`. Hence three layers, all fail-closed:

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

**Filters.** `src/sam/fmt.py:452-486` `register_jinja_filters(app)` writes
`app.jinja_env`, so a standalone `jinja2.Environment` gets none of
`fmt_number` / `fmt_date` / `fmt_size`. Refactor it to accept **any**
`Environment` — the body only touches `.filters` and `.globals` — keeping a thin
app-taking wrapper for the existing call site.

Be honest about why, because this was justified twice and both times wrongly.

**Not** "a shipping template needs a filter": the four expiration templates
render exactly seven variables — `recipient_name`, `project_code`,
`project_title`, `latest_expiration`, `grace_expiration`, `project_lead`,
`project_lead_email` — every one a plain string pre-formatted by the caller.

**Nor** "a DISK notice tells a PI their TiB-years are core-hours". It does
not, because **no expiration template renders the resource table at all**.
`src/cli/project/commands.py:346` builds `resources` with a hardcoded
`'units': 'core-hours'` and nothing downstream ever displays it. That is dead
data, not a mis-rendered notice, and it is worth fixing only because it is one
line and the value is wrong the moment anything *does* render it.

The real justification is forward-looking: the **new** `xras_activation`
template (§ 1) renders a per-resource allocation table, which is the first
notification body that has to state a unit. Getting that right needs
`alloc_unit` (`fmt.py:479-482`, and correct — it also returns `None` for an
access-boolean grant, so the table stops reading "1 hours"), and a standalone
`Environment` cannot reach it until `register_jinja_filters` stops writing
`app.jinja_env`. So: a small refactor with one new consumer, not a bug fix.

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
`--force` overrides. **Dry-run writes no row** — a preview is not an attempt,
and a stray `suppressed` row would poison the dedup query.

Three details that look like footnotes and are not:

**The key uses the *intended* recipient, before any redirect.**
`NOTIFY_REDIRECT_TO` rewrites the address, so a key built afterwards collapses a
whole staging run onto one key and the second project suppresses against the
first — suppression would behave differently in staging than in production,
defeating the point of having a staging mode.

**`already_sent(session, dedup_key, since=None)` — all time by default.** A
30-day window reads as the conservative choice and is wrong: both key formats
already carry their own window (`latest_end_date`, `xras_action_log_id`), so a
new expiration date or a new XRAS action mints a new key and is never
suppressed. A time window on top would silently re-enable the re-email bug for
anything older than it.

**⚠️ A stale `queued` row must not suppress its own retry.** `queued` is in the
match because a process that died *after* handing the message to the relay must
not re-send — but that is the same row a process that died *before* the relay
leaves behind, and the two are indistinguishable. Left alone, one crash
suppresses that recipient **permanently**, `--force` the only recovery. So the
`queued` arm is qualified by
`creation_time > now - NOTIFY_QUEUED_STALE_SECONDS` (300, an order of magnitude
above `MAIL_TIMEOUT`): fresh means "in flight, leave it", stale means "we never
learned, try again". Same query as the card's **"Queued (stuck)"** counter
(§ 8), which is how an operator learns the crash happened at all.

---

## 6. The subscription table, and why it was cut

An earlier draft shipped a second table, `notification_subscription`, **dormant**
— DDL, ORM, factory and tests, no reader — on the argument that a new SAM table
costs external DBA lead time (§ *Why this is worth doing now*, item 3), so it
should ride the ticket already open.

**That argument does not survive being stated.** It weighs one ticket against
zero; the real comparison is one against *two*, because a dormant table has no
consumer to validate its shape and altering a wrong shape is the same DDL, the
same ticket, the same lead time. Shipping it early buys nothing and pre-commits
a feature nobody has specified — per-user or per-role, project scope meaning the
project or its tree, whether an override address is wanted at all given
`user.all_emails` exists. Those want a feature request, not a schema guess. So:
**one table on the ticket**, and subscriptions get their own when somebody has a
use case.

One idea survives, because it is a Python decision that can be made now:
**"no row" must not be the policy — the kind must be.** `NOTIFICATION_KINDS`
declares `default_subscribed: bool` (§ 1, `kinds.py`). Expiration notices are
`True` — transactional, a PI must be told, and an absent preference row must
never silence that. Operational feeds like `xras.action_failed` are `False`,
opt-in. Any future table then expresses only *deviation* from the kind's
default, which is what keeps it small.

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
`display_notification_preview`. Add `--force` to override suppression.

Three one-liners get fixed while the file is open:

- **`:392`** — `'project_lead_email': project.lead.primary_email` is unguarded,
  two lines below a `project_lead_name` that *is* guarded (`:375`). An earlier
  revision claimed this `AttributeError`s on a lead-less project and aborts
  the run for **every** project. ⚠️ **Measured, that is not reachable.**
  `project.project_lead_user_id` is `NOT NULL` **and** carries an enforced FK
  (`project_lead_user_fk`; the snapshot has 0 dangling rows), so `project.lead`
  is never `None`; and `primary_email` *returns* `None` rather than raising
  when a lead has no address. Guard it anyway, for consistency with the line
  above — but the case the templates actually have to survive is
  `project_lead_email is None`, which **is** reachable (one snapshot project
  is in exactly that state) and now has tests.
- **`:346`** — the hardcoded `'units': 'core-hours'`, per § 4. Dead data
  today; wrong the moment a template renders it.
- **`:377-379`** — dead debug code: a commented-out `recipients = {}` reset then
  a hardcoded `benkirk@ucar.edu`, on a loop variable leaked from `:355`.
  Uncommenting it silently redirects every notice — which is what
  `NOTIFY_REDIRECT_TO` exists to do properly.

**The `Bcc` is a config move, and the interesting part is not to break it.**
An earlier revision of this doc claimed `email.py:127,138` leaks the header,
since it sets `msg['Bcc']` and never deletes it. **Measured, that is wrong**:
`smtplib.send_message` extracts the envelope recipients from `To`/`Cc`/`Bcc`
and then serialises the message *without* `Bcc`, so today's blind copy really
is blind. So `NOTIFY_BCC` only replaces a hardcoded address with config.

What makes it worth a test anyway is that the rewrite can easily lose the
property. A transport that builds its own recipient list and calls
`sendmail(from_addr, [recipient], msg.as_string())` — the obvious shape once
you are computing envelope recipients yourself — serialises whatever headers
are set, `Bcc` included. `SmtpTransport` therefore never sets a `Bcc` header at
all: the address goes only into `to_addrs`. § 10's assertion is a regression
guard on that, not a bug fix.

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
   Relay                ndir.ucar.edu:25   (STARTTLS)
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
**No chart.** An earlier draft put a `NotificationActivityChart`
(`StackedSeriesChart` over sent / failed / suppressed / redirected per day)
above the table; it is cut. The chart contract is not one class — it is a
`cache_name`, `LAYOUTS` at three sizes, a `chart_view` binding whose *position*
in `charts/__init__.py` is load-bearing (that order is the admin Caching card's
row order), a `chart_samples.py` case, and fingerprints regenerated at 3 layouts
× 2 themes, which makes every later visual change to this page a snapshot
negotiation. What it would show is four counts over time: the facet chips
already give the counts, the table gives the time, and the page this is modelled
on has no chart either. Add it if the shape of the traffic ever becomes a
question somebody actually asks.

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

## 9. Deployment plumbing — and the relay, measured

An earlier draft carried "can the pods reach the relay at all?" as this design's
one non-DBA external unknown and a launch blocker for the webapp half. **Probed
2026-08-09; it is not a blocker.** All measurement, from pod
`samuel-74b54c7f8c-2m6b6` (node `nwc1w10`, ns `sam-queries`, context `nwc1`):

| probe | result |
|---|---|
| DNS | `ndir.ucar.edu` → `128.117.64.127` |
| Banner | greets as `vscani3.ucar.edu` |
| `EHLO` | `PIPELINING SIZE=104857600 VRFY ETRN STARTTLS ENHANCEDSTATUSCODES 8BITMIME DSN SMTPUTF8` |
| `AUTH` | **not advertised** |
| `MAIL FROM:<sam-admin@ucar.edu>` | `250 2.1.0 Ok` |
| `RCPT TO:` an `@ucar.edu` address | `250 2.1.5 Ok` |
| `RCPT TO:` an **external** address | `250 2.1.5 Ok` — see § 3 |
| `STARTTLS` | negotiates; identical capability set after |
| Pod egress IP | `128.117.217.72` / `.73` — the **node** address, one per replica |
| `ucar.edu` SPF | `v=spf1 ip4:128.117.0.0/16 … ?all` → both egress IPs **pass** |
| `_dmarc.ucar.edu` | `p=none; sp=none` |
| NetworkPolicy | only `samuel-redis-allow-webapp`; **no egress restriction on the webapp** |
| End to end | one real message, pod → `benkirk@ucar.edu`, **delivered to the inbox** |

Four things follow, each replacing a hedge:

1. **No egress work.** No firewall rule, no NetworkPolicy change.
2. **No ExternalSecret for `MAIL_PASSWORD`** — no `AUTH` is advertised, so there
   is no credential to inject. Keep the config keys for a future relay; nothing
   in `helm/` references them.
3. **`MAIL_USE_TLS: "true"`** — STARTTLS is offered and works (§ 2).
4. **`sam-admin@ucar.edu` is deliverable from the cluster.** The worry that it
   might be spam-foldered invisibly — SMTP `250`, no inbox — was tested rather
   than reasoned about. Caveat: the egress IP is the *node's*, so a future node
   outside `128.117.0.0/16` falls out of SPF (though `?all` is neutral, not a
   hard fail).

`helm/` has **zero** `MAIL_*` today. `helm/values.yaml`'s `webapp.env` block
gains `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_DEFAULT_FROM`,
`MAIL_TIMEOUT`, `NOTIFY_ENABLED` and `NOTIFY_TRANSPORT`.

`src/webapp/logging_config.py:68` wires `('job_history', 'fs_scans')` as extra
logger roots because they inherit the root logger the app never configures. Add
`'sam'` — otherwise `sam.notify` emits nothing and a send is unobservable from
inside the app, which is the same silence that comment was written about.

**Nothing schedules a send.** No CronJob exists in `sam-queries`, and
`scripts/cron/accounting/crontab` is workstation→ssh→casper/derecho, accounting
only — so `--upcoming-expirations --notify` stays operator-invoked by hand. Left
alone deliberately (a scheduled sender wants the ledger proven first), but it is
*why* § 5's suppression is load-bearing rather than a nicety: an operator
re-running the command is the normal case, not the exceptional one.

---

## 10. Tests

New `tests/unit/test_notify_*.py` replace
`tests/unit/test_email_notifications.py` (10 tests) and
`test_notification_enhancements.py` (12). The mocked-`smtplib` assertions port
over largely unchanged — though all seven patch the import site
`'cli.notifications.email.smtplib.SMTP'`, so every one of those strings moves.

**Five of the 22 must be rewritten, not ported.**
`test_notification_enhancements.py:200-268` (grace-period, role, facility ×3)
each build a `MagicMock`, re-implement the production rule *in the test body*,
and assert against their own copy — they would pass if `commands.py` were
deleted. `test_role_determination_logic:220-225` doesn't even copy the right
algorithm: it compares `user_id` where production does precedence-by-overwrite
on an email-keyed dict (`commands.py:355-366`). Rewritten, they call the real
audience/payload builders, which today have no test touching them at all.

⚠️ **And the lift moves 266 lines into the coverage denominator for the first
time.** `[tool.coverage.run] source` (`pyproject.toml:150`) is
`["src/sam", "src/system_status", "src/webapp"]` — **`src/cli` is not
measured**. Code moving to `src/sam/notify/` starts counting against
`fail_under = 75.0`, and those five tests contribute nothing to it. Porting
rather than rewriting them is what turns this into a red build.

- `test_notify_render.py` — facility resolution + `DEFAULT_FACILITY_TEMPLATE`,
  text-only fallback, `sam.fmt` filters present in the notify env
- `test_notify_smtp_transport.py` — TLS, timeout, `TransportError` mapping,
  multipart vs plain, and `NOTIFY_BCC` **as an envelope recipient with no
  header emitted** — a regression guard, per § 7, not a bug fix
- `test_notify_service.py` — the guard matrix (disabled → `suppressed`,
  redirect → `redirected` + `intended_recipient` + a key built from the
  *intended* address), the `queued → sent|failed` lifecycle, one `open()` per
  batch rather than per message, the `on_result` callback, dry-run writing no row
- `test_notify_config.py` — the Flask-config-or-env seam under both contexts
- `test_notification_log.py` — `create()` vocabulary validation, the dedup
  query, and ⚠️ **the stale-`queued` case**: a fresh `queued` row suppresses,
  one older than `NOTIFY_QUEUED_STALE_SECONDS` does not. That is the § 5
  deadlock, and it is invisible without a test that manipulates the clock.
- `test_notifications_queries.py` — `summarize_notifications`, including the
  self-exclusion property; the failure it prevents is a chip strip that reads
  all-zeros the moment one filter is picked
- `tests/factories/notify.py` — `make_notification_log`, delegating to the
  model's own `create()` (`tests/factories/xras.py:103-119` is the pattern,
  including its xdist hazard note about deriving synthetic keys from DB-assigned
  PKs rather than a process-local counter)
- route tests for the changed `xras_notify*` endpoints and the two admin
  surfaces — auth / validation / render smoke, per the house convention that
  happy-path writes are covered at the model layer. ⚠️ Including the **negative**
  permission case: `VIEW_SYSTEM_CONFIG` alone gets the card and is **403'd** off
  the log page, because that boundary is the only thing keeping recipient
  addresses off a lower tier.
- `tests/integration/test_schema_validation.py` picks the table up
  automatically once it is exported from `src/sam/__init__.py`

**Structural gates**, in the repo's habit of making a silent failure loud:

- **(a) no socket, ever.** An autouse fixture in `tests/conftest.py` replaces
  `smtplib.SMTP`/`SMTP_SSL` with a raiser. A test that reaches a real relay
  fails with a sentence saying so, rather than mailing someone. Structural, not
  belt-and-braces: § 9 established that any host on the VPN reaches the relay
  and is accepted for arbitrary recipients, so the only reliable defence is that
  the socket cannot open.
- **(b) kinds ↔ templates are bijective.** Every `NOTIFICATION_KINDS` entry
  resolves a text template for every `FacilityName`, and every file under
  `notify/templates/` is reachable from some kind — catching a kind with no
  template *and* an orphaned template.
- **(c) route-map parity** — `tests/unit/test_route_map_parity.py` regenerated
  with `ROUTE_MAP_REGEN=1` in the same commit as the new routes.

⚠️ Exporting `NotificationLog` from `src/sam/__init__.py` **auto-registers a
Flask-Admin view** under "Everything". `tests/unit/test_admin_defaults.py`
spot-checks rather than pinning the full set, so it should not break — but the
view will exist, and `notification_log` holds addresses while `/database` is
kill-switched off only in production.

---

## Commit series

Each commit individually green.

| # | commit | contents |
|---|---|---|
| 1 | `feat(notify): channel-agnostic framework` | base types incl. `Transport.open()/close()`, config, renderer, transports, registry. No callers, no DB. |
| 2 | `feat(notify): move the expiration templates into the library` | delete the symlinks, `DEFAULT_FACILITY_TEMPLATE`, `package-data` + `jinja2`, `register_jinja_filters(env)`, the `alloc_unit` fix |
| 3 | `feat(db): notification_log` | `zz-92`, ORM model, `sam/__init__.py` export, schema validation, factory, `anonymize_sam_db.py` purge rule |
| 4 | `feat(notify): the ledger` | `queued→sent` lifecycle, suppression (intended-recipient key, `since=None`, the stale-`queued` horizon), the guard matrix |
| 5 | `refactor(cli): expiration notices run on sam.notify` | delete `src/cli/notifications/` **and** `cli/core/context.py:29-35`, rewrite the 22 tests, `--force`, fix `project_lead_email` + the dead debug block, `NOTIFY_BCC` as an envelope Bcc |
| 6 | `feat(webapp): the XRAS Notify button sends mail` | preview modal, real send, `xras_notify_manual_fallback.html`, route-map snapshot |
| 7 | `feat(admin): Notifications card on the Configuration tab` | `sam/queries/notifications.py`, the `config_inspect` block, the tile, the permission split |
| 8 | `feat(admin): the notification activity log` | page, facet chips, table, detail modal, route-map snapshot. **No chart.** |
| 9 | `feat(deploy): MAIL_*/NOTIFY_* in helm, .env.example, logger roots` | incl. `MAIL_USE_TLS: "true"`; no ExternalSecret |
| 10 | `docs: Sprint D as built` | this doc flipped to ✅, `XRAS_REIMPLEMENTATION.md` § Sprint D, `XRAS_CUTOVER_RUNBOOK.md:230`, `XRAS_SPRINT_B_FOLLOWUP.md` § *The adjacent bug* (now fixed), `src/webapp/README.md`, `docs/TESTING.md` counts, and a CLAUDE.md § *Notifications* |

---

## Verification

```bash
# 1. Pick up the new table (make docker-down has NO -v — this dance is required)
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
# re-run the same command: every row should now be skipped by suppression.
# Then prove the stale-queued escape hatch, which is the one path no amount of
# clicking reaches — age a row past the horizon and confirm it stops suppressing:
mysql -u root -h 127.0.0.1 -proot sam -e \
  "UPDATE notification_log SET status='queued', creation_time=creation_time - INTERVAL 1 HOUR LIMIT 1"
# re-run: that one recipient is attempted again; the others stay suppressed.

# 5. Webapp — the XRAS Notify path
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=console NOTIFY_REDIRECT_TO=you@ucar.edu \
  docker compose up webdev --watch     # → http://localhost:5050
#   Allocations → XRAS → pending card → Notify: the preview modal renders the body;
#   Send records notification_log + xras_activation_event and the card reads "notified".
#   Then with NOTIFY_ENABLED=0: the manual-fallback dialog, no event written, no 500.

# 6. The admin surface
#   Admin → Configuration → the Notifications card carries the counts from steps 4-5
#   and the "Redirecting to" line in warning colour.
#   Details » → the activity log: every facet chip switches (never all-zeros), and
#   a row modal names the template actually used.
#   As a user with VIEW_SYSTEM_CONFIG but not SYSTEM_ADMIN: card renders, page 403s.

# 7. Re-run the § 9 relay probe if it is ever doubted (read-only, no message sent)
POD=$(kubectl get pods -n sam-queries -l app=samuel -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n sam-queries "$POD" -- python -c "
import smtplib
s = smtplib.SMTP('ndir.ucar.edu', 25, timeout=10)
print(s.ehlo('samuel.k8s.ucar.edu')); s.starttls(); print('STARTTLS ok'); s.quit()"
```

**Handoff items that leave the repo:**

- **Add `notification_log` to PR #424's DBA ticket.** That ticket is a
  transcription of the `zz-9*` files; `zz-92` joins `zz-90`/`zz-91`. Filing
  separately costs another round of external lead time — the mistake `zz-91`'s
  header exists to prevent. (One table, not two: § 6.)
- **Staging needs all three run by hand once** —
  `infrastructure/scripts/init-rds.sh` restores the raw `.xz` with no initdb hook.
- **Nothing schedules the expiration notices** (§ 9). Whether that should become
  a CronJob is a separate decision, deliberately not taken here; until it is,
  the sender is an operator at a terminal and suppression is what makes a
  re-run safe.

---

## Risks and open questions

**The two that carried external dependencies are closed** — probed 2026-08-09,
§ 9. The pods reach the relay, no egress rule is needed, no credential is
needed, `sam-admin@ucar.edu` is accepted and SPF-passes, and a real message from
a pod landed in an inbox rather than a spam folder. Neither is a launch blocker,
and the DBA ticket is now the design's **only** external dependency.

What remains:

1. **Fail-closed means someone must set `NOTIFY_ENABLED=1` at launch.** Mitigated
   by the startup warning, but it is a real footgun. The alternative footgun is
   mailing production users from a laptop — and § 9 measured that such a laptop
   can reach arbitrary internet recipients, so the asymmetry is not close.
2. **The CLI's observable behaviour changes.** Suppression will skip recipients
   already mailed against the same key. That is the bug fix, but it is a change;
   `--force` is the escape hatch, and the stale-`queued` horizon (§ 5) is what
   keeps a crash from needing it.
3. **Two clicks instead of one on XRAS Notify.** Recommended, because the act
   becomes irreversible — but it is a UX change to a surface that has already
   been reviewed.
4. **Commit 5 is the risky one, and it is risky for a non-obvious reason.** Not
   the mailer move — the coverage denominator (§ 10). 266 lines enter a
   `fail_under = 75.0` gate that never measured them, in the same commit that
   rewrites the tests meant to cover them.
5. **Nothing here changes what XRAS itself receives.** These are NCAR-originated
   notices only; the broker contract is untouched.

---

## As built

Ten commits on `smtp_notify`, plus two corrections to this document made
*before* the code they would have misdirected. Everything in § 1–§ 10 shipped
as described except where noted below.

### ⚠️ Four claims this document made that measurement disproved

Each was a plausible reading of the code. Each was wrong, and each is
corrected in place above rather than left for the next reader to re-derive.

| claim | what was measured | consequence |
|---|---|---|
| The hardcoded `Bcc` ships on the wire, so every recipient can read it (§ 7) | `smtplib.send_message` extracts envelope recipients from `To`/`Cc`/`Bcc` and serialises **without** `Bcc`. Today's blind copy is blind. | `NOTIFY_BCC` is a config move, not a correctness fix. The test survives, reframed: a transport passing `to_addrs` explicitly *would* transmit the header, so `SmtpTransport` never sets one. |
| A DISK/ARCHIVE notice tells a PI their TiB-years are core-hours (§ 4) | The four expiration templates render exactly seven variables and **none renders the resource table**. The hardcode is dead data. | Fixed anyway (one line), but `register_jinja_filters(env)` is justified *forward* — its consumer is the new `xras_activation` template, the first body that states a per-resource unit. |
| `project.lead.primary_email` AttributeErrors on a lead-less project and aborts the run for every project (§ 7) | `project_lead_user_id` is `NOT NULL` **and** the FK is enforced (0 dangling rows); `primary_email` *returns* `None` rather than raising. | The crash is unreachable. The guard is kept for consistency; the reachable case — a lead with no address, one such project in the snapshot — got four tests. |
| *(implicit)* importing a submodule avoids the package's imports | It does not: `import sam.notify.models` runs `sam/notify/__init__.py` first. | Broke `webdev` startup. `sam/notify/__init__.py` is now lazy (PEP 562); `tests/unit/test_notify_import_graph.py` is the gate. |

### Deviations from the plan

- **`register_jinja_filters(env)` moved from commit 2 to commit 1**, because
  `render.py` needs it to be complete. Commit 2 kept the template move,
  packaging and the `alloc_unit` fix.
- **`cli/notifications/email.py` was pointed at the new template directory in
  commit 2** rather than given a copy, so every commit stays green without a
  duplicate of four files that would silently diverge before commit 5 deleted
  the module.
- **`--force` reuses the existing flag** rather than adding one. It already
  meant "skip the confirmation prompt" for `--deactivate`; it now means "skip
  the protection" on both surfaces, and the validation reads
  `--force requires --deactivate or --notify`.
- **`webapp/run.py` drops its own directory from `sys.path`.** Beyond the
  reported bug, and optional — but `python3 src/webapp/run.py` makes
  `src/webapp` `sys.path[0]`, where `config` resolves to `webapp/config.py`
  instead of `src/config.py`. Whether that fires depends on import *order*,
  which is what let an unrelated change arm it.
- **Four notification statuses joined the shared `status_badge` vocabulary**
  rather than getting a private colour map.

### Not built, and why

- **`stuck_queued` has no alert beyond the card.** The counter is there; who
  gets told is a policy question nobody has asked yet.
- **Nothing schedules a send** (§ 9). Still true, still deliberate, and still
  the reason suppression is load-bearing rather than a nicety.

### Verified by hand, not only by the suite

Run inside the compose network against the **obfuscated** test database
(never the dev DB, which may hold real production rows):

```
sam-admin project --upcoming-expirations --notify   →  602 sent
same command again                                  →  0 sent, 602 skipped
age one queued row past the horizon, re-run         →  exactly 1 retried,
                                                        601 still skipped
```

The third line is the § 5 deadlock. Without the staleness horizon that one
recipient would have been suppressed permanently, and no amount of clicking
reaches the state — it needs a crash between the `queued` write and the
outcome write.

### Coverage

`src/cli` is outside `[tool.coverage.run] source`, so the lift put 571
statements under `fail_under = 75.0` for the first time — in the same commit
that rewrote the tests meant to cover them (risk #4). Measured after:
**`src/sam/notify` 571 statements, 6 missed, 98.95%**.

### Still outstanding

- ~~**`zz-92` on PR #424's DBA ticket.**~~ ✅ **Closed 2026-08-10.** `hpc-writer`
  was granted DDL and all three tables were created in production by hand; the
  `zz-9*` scripts have since been retired entirely. The design has **no
  remaining external dependency.** See `DBA_PRIVILEGE_REQUEST.md`.
- **ECS-staging's RDS still lacks the tables.** `infrastructure/scripts/init-rds.sh`
  restores the `.xz` with no initdb hook, and it is a one-time bootstrap — the
  regenerated snapshot carries all three, but an existing instance never re-runs
  it. CIRRUS/k8s is the deployment target, so this is a render-check concern.
- **Whether the expiration notices should become a CronJob** — deliberately
  not decided here.
