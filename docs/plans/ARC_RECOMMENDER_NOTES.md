# ARC Allocation Recommender notes on XRAS requests

**Status: planned, not built.** Branch `xras-recommender-notes` (this document is its first
commit). Two PRs: this repo (the table, the endpoint, the dialog and the card), then
`NCAR/arc-allocation-recommender` (the poster). The decisions in § 8 are Ben's (2026-11-05).

## Progress

- [x] `XrasRequestNote` model, `scripts/sql/create_xras_request_note{,_pg}.sql`, schema pins, factory
- [x] `Permission.POST_XRAS_NOTES`, the default grant to `apikey:heuv`
- [x] `POST /api/v1/xras/notes`: blueprint, form schema, `record_run_notes`, output schema, tests
- [x] Dialog section `_xras_recommender_note.html` with the copy button and source links
- [ ] Card marker on the remediation card, one query per render
- [ ] Rename follows the note (`_rekey_overrides`)
- [ ] DDL applied: local, test container, LFS blob, `sam_dev`, prod; RBAC grant on dev and prod
- [ ] Dev smoke: one hand-posted batch, the dialog and the card in a browser
- [ ] PR 2 in the recommender: `SamNotesClient`, `sam_notes_enabled`, the hook after `save_run`

## 1. What is true

The XRAS **approver's note** is `adminComments`. SAM reads it from the reports feed under the
`report` context (`src/sam/integration/xras_api/comments.py`) and shows it read-only in the request
dialog; the pencil on "Comments" edits the submitter's `userComments` through
`PUT /v1/requests/<rid>/actions/<aid>` under `submit`. No route in the XRAS ALLOCATIONS API writes
an admin or reviewer comment (the apidoc index, 86 routes, read 2026-11-05; `GET .../reviews` is
read-only), and our key is provisioned for `submit` and `report` only: `review` and `admin` answer
401 on every endpoint and every identity, including a person who holds `administrator` and
`review-impersonator` in XRAS (`docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md` § 4.2,
`XRAS_DEV_PROBES.md`). Every `adminComments` value on record was typed into the admin app's
Process tab. Writing it from SAM needs an admin-context key from Steve and a probe of whether the
update-action route takes the field at all; that is out of scope here.

The **ARC Allocation Recommender** (`NCAR/arc-allocation-recommender`) runs weekday mornings at
06:00 MT: it reads the XRAS review queue, reads SAM's HEUV usage report under the `heuv` API key
(`docs/apis/HEUV_API.md`, "Callers"), applies the written approval rules, has a model write the
prose, saves the run, and posts a digest to Slack `#arc-allocations`. Per request it produces a
`Recommendation`: the decision (`approve`, `reject`, `route_to_human`), the Process-page values
(button, end date, hours per resource), the requested hours, a draft `admin_comment`, a
`rationale`, a `pi_history` line, `findings[]` (`id`, `severity` ok | info | warn | block,
`message`, `sources[]` of `{label, url, kind}`), citations and a `ruleset_version`, keyed by the
XRAS `request_id`, `action_id` and `number`. Its Slack card shows "Needs your judgement" (warn and
block findings) and "Already checked, with the source" (ok and info), every source a link, and an
"Open in XRAS" link to the action's Process page. It recommends; a person confirms in XRAS.

SAMuel carries that recommendation on the request, so the operator who opens the request here sees
the decision, the values, the draft comment with a Copy button next to the Process-tab link, and
the findings with their links, and pastes the comment into XRAS by hand.

## 2. The note

Table `xras_request_note`, one row per `(request_number, source)`, the shape of
`xras_request_override` (`XrasRequestOverride`, `src/sam/integration/xras.py`):

| column | type | meaning |
|---|---|---|
| `request_number` | VARCHAR(128), PK | NCAR#### or the projcode: what the card, the reports and the operator key on. Never the volatile `request_id` |
| `source` | VARCHAR(32), PK | from the body, `arc-allocation-recommender` |
| `request_id`, `action_id` | INT NULL | informational; `action_id` is the Process-page link's target |
| `run_id` VARCHAR(64), `generated_at` DATETIME, `ruleset_version` VARCHAR(32) | | the run stamp the dialog shows as "as of" |
| `decision` VARCHAR(16), `button` VARCHAR(32) NULL, `end_date` DATE NULL | | the Process-page values |
| `values_json` TEXT | | `{hours_by_resource, requested_hours, links}` |
| `admin_comment` TEXT, `rationale` TEXT, `pi_history` VARCHAR(500) | utf8mb4 | prose |
| `findings_json` MEDIUMTEXT utf8mb4 | | `[{id, severity, message, sources: [{label, url, kind}]}]` |
| `created_by` VARCHAR(35), `creation_time` DATETIME, `modified_time` TIMESTAMP, `active` | | `created_by` is the API key's username; app-clock, naive Mountain |

`XrasRequestNote(Base, SessionMixin, ActiveFlagMixin)` sits beside the override with `set()` (upsert
the one row for its key, `active` back on), `rekey(old_number, new_number)` and `clear()`; module
lookups `lookup_request_notes(session, request_number)` and `request_notes_by_number(session,
numbers)` (one `IN` query). No foreign keys: every id is XRAS-owned. Latest note only: each run
overwrites the row; the run history lives in the recommender's own store.

DDL: `scripts/sql/create_xras_request_note.sql` (MySQL; header per
`create_xras_request_override.sql`: the "Apply with" line, no DROP, utf8mb4 `ALTER`s on the prose
columns, verification `SELECT`s) and `create_xras_request_note_pg.sql` (ends `OWNER TO sam_dev`).
Pins: the utf8mb4 column list in `tests/integration/test_schema_validation.py`, `_BOOTSTRAP_TABLES`
in `tests/conftest.py`, the anonymizer purge list, `make_request_note` in `tests/factories/`.
`sam/__init__.py` imports the model so `/database` reflects it.

## 3. The endpoint

`POST /api/v1/xras/notes`, blueprint `src/webapp/api/v1/xras_notes.py` registered in `run.py`
beside the other `api/v1` blueprints. The `/api/xras/v1` blueprint is XRAS's own inbound contract
and stays untouched. Decorators as the status collector's POSTs (`src/webapp/api/v1/status.py`):
`@csrf.exempt` and `@api_key_required(permission=Permission.POST_XRAS_NOTES)`, so the route is key
only, carries the machine rate limit, and under `RBAC_SOURCE=db` admits only a key granted the
permission. `Permission.POST_XRAS_NOTES = "post_xras_notes"` joins the XRAS group in
`src/sam/security/permissions.py`; no `ALL_*` aggregate matches it, so it fails closed;
`rbac_defaults.py` grants it to `apikey:heuv` (the recommender's existing credential; a dedicated
`recommender` key later moves the grant). Prod and dev get the grant by hand:

```
sam-admin rbac --grant apikey:heuv --permission post_xras_notes --note "ARC recommender notes"
```

One batch per run, so a forty-card morning is one call:

```json
{"source": "arc-allocation-recommender", "run_id": "2026-11-05T06:00", "generated_at": "2026-11-05T06:01:00-07:00",
 "ruleset_version": "2026.10", "notes": [
  {"request_number": "NCAR4473", "request_id": 1454171, "action_id": 410334,
   "decision": "route_to_human", "button": null, "end_date": null,
   "hours_by_resource": {"Derecho": 1000000}, "requested_hours": {"Casper": 20000, "Derecho": 1000000},
   "admin_comment": "...", "rationale": "...", "pi_history": "This person has 1 past request on record.",
   "findings": [{"id": "new.us_university", "severity": "warn", "message": "...",
                 "sources": [{"label": "NSF award 2320667", "url": "https://www.nsf.gov/awardsearch/...", "kind": "nsf"}]}],
   "links": {"xras": "https://admin-ncar.xras.org/actions/410334/show_process"}}]}
```

Validation in `sam/schemas/forms/xras_notes.py` (`RecommenderNoteBatchSchema`,
`RecommenderNoteSchema`, exported from `forms/__init__.py`): `decision` is one of the three values
and `severity` one of the four; every `url` is `http(s)` (anything else, or a blank, is a 400); at
most 200 notes and 40 findings each; caps of 4000 on the comment and rationale, 1000 on a message
and a URL, 200 on a label; `generated_at` ISO, an offset converted to naive Mountain (CLAUDE.md
§ 1). The body is read with `request.get_data()` under a 1 MB cap. The write is
`sam/manage/xras_notes.py::record_run_notes(session, *, source, run_id, generated_at,
ruleset_version, notes, created_by)` inside `management_transaction`, `created_by` the key's
username. The reply is a Marshmallow output schema: `{"source", "run_id", "received", "request_numbers"}`
200; a 400 carries `{"error", "details"}` through `register_error_handlers`.

## 4. The dialog and the card

The request dialog (`xras_request_detail.html`, `MANAGE_XRAS`, so operators only) includes
`_xras_recommender_note.html` after the title and abstract, rendered only when `_detail_context`
(`modals.py`) finds a note for the request; no note, no section.

- `section_label('Recommender')`; the decision as `tag` (approve) or `attention` (route, reject),
  the phrase map `approve` "Ready to approve", `reject` "Recommend rejecting", `route_to_human`
  "Needs a person to review"; the stamp "as of <generated_at> · ruleset <version> · run <id>".
- `.modal-facts`: button, end date (`fmt_date`), hours per resource (`fmt_number`), requested hours.
- The draft admin comment in a quiet block with
  `copy_button(note.admin_comment, label='Copy admin comment', done='Admin comment copied.')`
  (`fragments/clipboard.html`; never inside a `data-bs-toggle` element) beside
  `xras_admin_links(note.action_id)`, so the paste target is the next click.
- Findings in two lists, "Needs your judgement" (warn, block) then "Already checked" (ok, info),
  each message with its sources as `<a href target="_blank" rel="noopener noreferrer">` plus the
  external-link icon; `pi_history` as one muted line; the rationale under
  `<details class="xras-disclosure">`.
- Every control swaps `#xrasModalBody`; nothing toggles a modal (#464); no inline script or style.

The remediation card (`xras_remediations_card.html`, `card_routes.py`) reads
`request_notes_by_number` for the page's rows at render time: the index is built by the sweep at
04:00 MST and the note lands at 06:00, so the card must not depend on the index. The request cell
gains one muted `fa-wand-magic-sparkles` icon in the `icon_count` style whose `title` reads
"Recommender: Needs a person to review · 2026-11-05". No facet, no count badge.

## 5. Rename follows the note

`_rekey_overrides` (`src/sam/manage/xras_remediation.py`) also calls
`XrasRequestNote.rekey(old, new)` on its private session, warning on a collision, so a note posted
on `NCAR####` survives the rename to the projcode as the overrides do.

## 6. Tests

- Model: `set()` twice leaves one row; `rekey`; `clear`; the schema-validation pins;
  `tests/unit/gates/test_layer_imports.py`.
- Schema: a `javascript:` or blank URL, an unknown decision or severity, and 201 notes are 400s.
- Endpoint (`tests/api/test_xras_notes.py`, the `api_key_client` fixture and the `db`-mode pattern
  of `tests/api/test_api_key_enforcement.py`): 401 without a key, 403 for a key without the
  permission under `RBAC_SOURCE=db`, 200 on an upsert twice, 400 on bad JSON, `created_by` the
  key's user.
- Dialog: the section with the copy button and the source links when a note exists, nothing
  otherwise, every rendered link `rel="noopener noreferrer"`.
- Card: the marker and its title; none without a note.
- Rename: `rename_request` re-keys the note.
- Gates: docs, CSP, template detectors, modal shell, CSS dead and tokens, route map, `tests/api`.

## 7. DDL and rollout

1. The MySQL script on the local clone (3306) and the test container (3307);
   `make -C containers/sam-sql-dev regen-lfs-blob`; `pytest tests/integration/test_schema_validation.py`;
   `make pytest-pg`.
2. Ben applies the `_pg` script on `sam_dev` (or `make refresh-dev` rebuilds it from the ORM) and
   the MySQL script on prod **before** the image that serves the endpoint rolls, then the RBAC
   grant on both.
3. Merge to staging; dev smoke: one `curl -u heuv:<dev key>` batch naming a request on the dev
   card, then the dialog (copy button, links) and the card marker in a browser.

## 8. Decisions

- The recommender posts with the **heuv** key now; a `recommender` key moves the grant later.
- **Latest note only**, upserted per run; history stays in the recommender's store.
- The note surfaces in the **dialog and as a card marker**; the PI's My Requests does not show it.
- The note's **links are followed as links**, validated to `http(s)` at ingest.
- Two PRs, this repo first; the recommender's poster is opt-in per environment.

## 9. PR 2: the recommender

In `NCAR/arc-allocation-recommender` (its own `CLAUDE.md` governs; the tool still never writes to
XRAS):

- `src/arc_recommender/clients/sam_notes_client.py`: `SamNotesClient(settings, client=None)` on the
  basic-auth `httpx.Client` shape of `clients/sam_client.py`; `post_run(report: RunReport)` builds
  the batch above from each `Recommendation` (`findings[].sources` as `{label, url, kind}`,
  `links.xras` from `manual_action.process_page_url`, a card without a `number` skipped) and sends
  one POST to `{sam_base_url}/api/v1/xras/notes`.
- `config.py`: `sam_notes_enabled: bool = False` (opt-in per env) and `sam_notes_url` (defaults
  to the SAM base). Secrets unchanged: the `sam` secret's username and password are the heuv key.
- `engine/run.py`: after `store.save_run(report)` and before the digest, post when enabled; a SAM
  failure logs one line and never fails the run or the digest; the count and status ride in the
  delivery dict so the digest overview can say "notes posted to SAM".
- Tests: a fake `httpx` transport asserting the batch shape, the opt-out default, a swallowed error.
- Docs: `docs/HANDOFF.md` (the daily cycle gains "post notes to SAM") and `docs/DESIGN.md` § 4.
- Dev points at `samuel-dev.k8s.ucar.edu` with the dev heuv key for the first run; prod flips
  `sam_notes_enabled` once PR 1 is on prod.

## 10. Out of scope

Writing `adminComments` in XRAS; showing the note to PIs; a decision facet on the card; a history
of runs in SAM; the dedicated `recommender` key.
