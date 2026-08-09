# XRAS pre-cutover tidy-up — what the stacked sprints left behind

> ✅ **Built.** Nine commits on `xras_reimplementation` (PR #424), `4871b9f`…`2cc7e1b`.
> Suite 5,233 → **5,246**. See [`## Deviations`](#deviations) — three findings were not
> duplication at all but *divergence*, and the refactor itself produced two bugs that
> the tests caught.

**Why this exists.** The six handlers, the ingest endpoint, the operator page, the query
layer and the CLI were built as stacked sprints over one long build. The domain core had
already been through a dedicated refactor ([`XRAS_HANDLER_REFACTOR.md`](XRAS_HANDLER_REFACTOR.md)),
but the Sprint A/B layers had not, and cutover is abrupt — see
[`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md). This was the last cheap moment to
change shape.

---

## The thing worth knowing

**Three of the findings were not tidiness. The duplication had already diverged**, in
each case in the direction that made an operator's job harder:

| # | The copies | What had drifted |
|---|---|---|
| 1 | `actions.py` and `replay.py` each spelled the 400/422 parse ladder | `replay.py` never passed `action_id`, so **every replayed row stored NULL** in the column the runbook's triage section reaches for first. Its docstring still said `actionId` was "not a column" — true when written, false since C.1b |
| 2 | The status zero-fill, written three times | The query layer keeps an out-of-vocabulary status *on purpose*; both consumers re-derived the dict from `XRAS_ACTION_STATUSES` and dropped it, while `total` still counted it. The summary did not reconcile with the sum of its own buckets |
| 3 | The `projcode_result` OR `request_number` join, written three ways | Two of them broke a same-second tie differently. The pending card could show one action as the reason a project was pending while `xras_activation_event` stamped a **different** one as the provenance |

Each was landed as a fix-with-failing-test-first, next to the collapse that removed the
duplication. That ordering is the argument for doing any of this before cutover rather
than after.

## What else landed

- **Plan records** (`handlers/_plans.py`) replace positional tuples that carried the same
  five values in **three different field orders**, plus a four-arity string-tag `elif`
  chain in `update.py`.
- **`Roster` carries the rows it resolved.** It looked every user up to validate them and
  threw the rows away; New and Update then re-queried all of them. Two further copies of
  the same shape fell out (one person in three roles paid three `SELECT`s; the mnemonic
  extractor made a fourth). ~22 queries → 10 for a ten-member action.
- **`_add_to_subtree`** — `supplement_allocation` and `adjust_allocation` were
  byte-identical 16-line bodies.
- **`queries/xras_activation.py`** split out of an 906-line module fusing two tables.
- **The pending card ran its own pipeline twice per render.**
- **Existing helpers used instead of hand-rolled ones**: `htmx_success`,
  `modal_triggers`, `read_flag`, `fmt_date`, plus a new shared `htmx_modal_not_found`.
- **`tests/xras_helpers.py` + `tests/factories/xras.py`** — `committing` ×7,
  `FIXTURE_DIR` ×12, `load_fixture` ×9, `wire_resource` ×5, `txns_for` ×4, and 16
  hand-rolled mapping inserts, collapsed to one definition each.

---

## Deviations

### The refactor produced two bugs. Both were caught, one only because a test was added first

**`PlannedCreate` captures `panel_authorised` at construction**, where the old loop read
it at execute time. In `new.py`, `_plan_allocations()` was called one line *before* that
flag was computed — harmless under the old scheme, silently `False` under the new one.
Every CREATE row on a panel-authorised New would have lost its flag.

Nothing would have caught it: `test_xras_new_handler.py` had **no** `auth_at_panel_mtg`
coverage at all, on the handler with the highest production failure rate, for exactly the
reason the Adjustment bug hid for a sprint — every test uses the default
`allocationType='Small'`, which is not panel-authorised, so the flag reads `False` either
way. `TestPanelAuthorisation` was added and verified to fail on the reordered version.

**The new factory minted mapping keys with `next_int`.** Unlike `next_seq`, that counter
bakes in no worker tag, so twelve xdist workers all mint `900_001` and collide on a
primary key. It surfaced as one intermittent error. Keys went back to
`_KEY_BASE + resource.resource_id` — `resource_id` is DB-assigned and therefore unique
across workers without coordination, which is what the seven per-module magic bases were
groping towards.

### One assertion was intentionally relaxed, and one intentionally changed

`test_every_status_appears_even_at_zero` asserted the vocabulary was *exactly* the five.
Since finding 2, a stray status is deliberately preserved — and the dashboard fixture
**commits** such a row (it must; route handlers see only committed rows), so another
worker's summary can legitimately observe it. `==` there fails intermittently and blames
the wrong test. Relaxed to `>=`, matching its CLI sibling.

`test_a_clean_action_resolves_lead_admin_and_members` compares a whole `Roster` by
equality, so widening the dataclass changed it. It now asserts the carried rows too.

### A declared behaviour change

Supplement and Adjustment applied all supplements/adjustments and *then* all creations,
because each kind had its own list. They now apply in wire order, interleaved. The two
branches are mutually exclusive per resource and touch different allocations, so no write
depends on the other; the grouping was an artefact of the two-list shape. No test pinned
it. **Update's ordering is unchanged** — one resource can emit three steps and that order
is legacy's.

### Explicitly not done

- **`filter_panel_shell` / `multiselect_filter` / `modal_title_oob` Jinja macros.** Real,
  ~7 call sites repo-wide each — but pre-existing debt XRAS merely joined, and
  `xras_filters.html:20-24` already argues correctly that a seven-template refactor does
  not belong in a feature change. Its count of "five places" is low; it is seven.
- **Migrating `_jobs_facet_chips.html` onto the neutral `facet_row`.** The CSS half was
  migrated; the Jinja half stopped one step short.
- **`sam/queries/_paging.py`.** `xras_actions.py` is the *third* copy of a
  sort/whitelist/paginate block (`allocations.py`, `charges.py`) — and the best of the
  three: its `_in` closure replaces an `isinstance` block those two spell nine times
  between them. Back-porting is a separate ticket, not #424's debt.
- **Auto-registering handlers via `__init_subclass__`**, which would remove the five
  `handle_X` + `register` trailers. `handle_extension` alone has 17 test call sites, and
  it would change the double-registration raise `test_xras_dispatch.py:421-449` probes.
  Four lines each is the cheaper side.
- **`sam/xras/__init__.py`'s 34-name re-export block**, which nothing in `src/` imports —
  every consumer uses submodules. Left alone because `test_xras_errors.py:202-210`
  asserts against it and the churn buys nothing this side of cutover.
- **The two "action type" vocabularies that differ by one character** —
  `xras_access.py`'s outbound `'Supplemental'` vs `xras_actions.py`'s inbound
  `'Supplement'`. Neither is wrong. Worth reciprocal docstring pointers eventually, in a
  codebase that already burned a sprint on a one-word field-name mismatch.

### Untouched by design

`webapp/api/xras/{__init__,people,requests,serialize}.py` and `queries/xras_access.py`
are legacy-compat: response bytes are the contract and runbook gate 3 is a byte-clean
parity run. `sam/xras/errors.py`'s 34 builders are byte-pinned by two tests that
enumerate the module's public callables — collapsing them into a table breaks those gates
by construction, which `XRAS_HANDLER_REFACTOR.md` § *Bug 6* already learned.
