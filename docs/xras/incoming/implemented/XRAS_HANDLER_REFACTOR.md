# XRAS handler refactor — the base class the six handlers should have shared

> ✅ **Built.** Five commits on `xras_reimplementation` (PR #424), `a2f8885`…`ee8b16a`.
> Suite 5,213 → **5,223**. See [`## Deviations`](#deviations) at the end — the doc's
> structural claims held, but a third of its *measurements* did not, and one bug was
> narrower than described. What follows is the design as written; the deviations record
> what execution changed.

**Handoff doc.** Written for a cold start: you should be able to execute this without the
session that produced it. Every claim below carries a `file:line` or a measurement, so
re-verify rather than trust — the code moves.

**Companion:** [`XRAS_STRESS_AND_SCHEMA.md`](XRAS_STRESS_AND_SCHEMA.md). Neither blocks
the other. Doing *this* first makes the stress harness materially cleaner (see
§ *Why the order matters, weakly*), but if the DBA ticket cannot wait, do that one first
and it will absorb the friction.

**Prior work:** Sprint C ([`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md)) shipped all six
handlers across twelve commits, suite 4,708 → 5,213.

---

## Why this exists

The six handlers were built **sequentially** — Extension, Supplement, Adjustment, New,
Update, Transfer — over one long session. The shape of a handler only became clear around
the third one. So the helpers ended up owned by whichever handler happened to need them
first, and the import graph now encodes *build order* rather than domain structure.

That is normally a cosmetic complaint. Here it is not, because **the duplication has
already produced a bug**.

### The bug that settles the argument

`src/sam/xras/handlers/adjustment.py` computes the panel-authorisation flag, carries it
through its creation tuple, unpacks it — and then never applies it:

| line | what happens |
|---|---|
| `adjustment.py:81` | `auth = auth_at_panel_meeting(session, action)` |
| `adjustment.py:112` | `creations.append((resource, amount, comment, start, end, auth))` |
| `adjustment.py:152` | `for resource, amount, comment, start, end, auth in creations:` |
| — | **`auth` is never read again.** No `_mark_panel_authorised` call follows |

`supplement.py:287` and `new.py:265` both do apply it. So an **Adjustment-created
allocation silently never gets `auth_at_panel_mtg` set**, and a Supplement-created one
does. Nothing caught it because each handler is tested against itself, and no test
compares two handlers' output for the same shape of input.

⚠️ Note that `adjustment.py:69-75` carries a docstring arguing *against* sharing `_plan`
with Supplement — "*a shared function with three flags reads worse than two functions
that each say what they do*". **That argument was wrong, and this is the evidence.** When
you delete that docstring, delete it knowingly.

### The second bug: a feature wired to nothing

`DispatchResult.warnings` is **read by nothing**. It is populated at `new.py:279` and
`update.py:297` and then discarded by `_dispatch` (`src/webapp/api/xras/actions.py:222-241`),
which never touches the field.

What is being dropped is the **legacy defect-3 roster disagreement** — the case where a
person is assigned as project lead but excluded from the project's own roster. Sprint C
built that warning deliberately, on the grounds that it is *"the only evidence anyone has
that this occurs"* (`XRAS_SPRINT_C.md` § *The roster*). The mechanism exists; the last six
inches were never connected.

---

## The sediment, measured

Findings below are from a full read of all twelve modules in `src/sam/xras/`. Line
numbers are as of Sprint C's final commit (`bd8a366`).

### Import graph follows build order

| module | cross-handler imports |
|---|---|
| `extension.py` | **none** — leaf |
| `transfer.py` | **none** — leaf |
| `supplement.py` | `:49` from `.extension`: `_get`, `effective_end_date`, `latest_allocation` |
| `adjustment.py` | `:52` from `.extension` (2) · `:53-60` from `.supplement` (6) |
| `new.py` | `:60` from `.extension` (2) · `:61-66` from `.supplement` (4) |
| `update.py` | `:82` from `.extension` (4) · `:83` from `.new` (4) · `:84-91` from `.supplement` (6) · **plus a function-body import** at `:222` (`from .new import _plan_contracts`) |

`update.py` imports **15 symbols from three sibling handlers, 9 of them
underscore-private**. Four handlers reach across module boundaries for `_`-prefixed names.
Nothing about the domain says Update depends on New; only the calendar does.

### Verbatim duplication

| what | copies | where |
|---|---|---|
| `_get(obj, key)` | **4 identical** + 1 inlined | `dispatch.py:254`, `extractors.py:301`, `roster.py:107`, `extension.py:61`; inlined at `transfer.py:70-71` |
| `_mark_panel_authorised()` | **3 byte-identical** | `supplement.py:292`, `new.py:304`, `update.py:300` |
| `projcode = (_get(action, 'requestNumber') or '').strip()` | **9 occurrences** | `extension:181`, `supplement:143,207,264`, `adjustment:76,142`, `update:209`, `dispatch:195`, + transfer's inline |
| `create_allocation(...)` 9-kwarg call | **4 occurrences** | `supplement:277`, `adjustment:153`, `new:254`, `update:271` |
| lead/admin/members resolution block | **2 verbatim** (6 lines) | `new.py:199-204`, `update.py:232-237` |

### Redundant work

`Project.get_by_projcode` is called **3× per Supplement action** (`supplement:144` inside
`auth_at_panel_meeting`, `:208` inside `_plan`, `:270` in the handler) and **2× per
Adjustment**. Nothing memoises it.

### Near-duplicates

`supplement._plan` (`:200-254`) vs `adjustment._plan` (`:67-131`): **~30 code lines
identical**, including the entire 8-line loop head. Three real differences — an alias
(`comment_for` vs direct call), a non-positive guard inserted in the create branch, and a
different sign gate plus the below-zero check in the non-create branch. Tuple arity
differs (4 vs 3), which is where the `auth` flag got lost.

**Two create policies, each duplicated:**

| policy | handlers | dates |
|---|---|---|
| *today + project history* | supplement, adjustment | `datetime.now()` at midnight; `new_allocation_end_date(project, start)` |
| *action dates + commission clamp* | new, update | `parse_action_begin_date` / `parse_action_end_date`, then `clamp_start_to_commission`, then the `end <= start` refusal |

`new.py:150-157` and `update.py:160-165` are the same six lines modulo a variable name.
Both policies are legacy's and **both must survive** — the point is to name them once
rather than let them drift apart the way the `auth` flag did.

### `management_transaction` has five import sites

`extension.py:38`, `supplement.py:40`, `adjustment.py:46`, `new.py:46`, `update.py:73`.
Each rebinds the name into its own module globals, so **every test that must neutralise it
patches five things** — see `tests/unit/test_xras_oracle.py:70-94`, which loops over all
five and says why. That hazard has already leaked rows into the shared test database once
(`XRAS_SPRINT_C.md` § *Extension* → *Testing hazard*).

---

## The target

### `src/sam/xras/handlers/base.py`

```python
class ActionHandler(ABC):
    service: ClassVar[str]                      # 'extend' | 'supplement' | ...

    def __init__(self, session, action):
        self.session, self.action = session, action
        self.errors = ActionErrors()
        self.warnings: list[str] = []

    def run(self) -> DispatchResult:            # the template method
        self.assemble()                         # pure — reports, writes nothing
        self.errors.raise_if_any()              # the single check point
        with management_transaction(self.session):
            self.execute()                      # the ONLY transaction boundary
        return self.result()

    @abstractmethod
    def assemble(self) -> None: ...
    @abstractmethod
    def execute(self) -> None: ...

    def get(self, key): ...                     # kills 4 definitions + 1 inline
    @cached_property
    def projcode(self) -> str: ...              # kills 9 occurrences
    @cached_property
    def project(self): ...                      # kills the 3× lookup
    @cached_property
    def auth_at_panel_meeting(self) -> bool: ...
    def mark_panel_authorised(self, allocation): ...   # kills 3 definitions
    def result(self, **kw) -> DispatchResult: ...      # carries service + warnings
```

`run()` is the assemble → check-once → execute contract that Sprint C established in prose
and then re-implemented six times. Making it a template method is what stops handler seven
from getting it subtly wrong.

**`transfer.py` overrides `run()`** in about three lines with a comment. It has no
assembly, no errors and no transaction; forcing it through the template would mean adding
a `has_work()` hook that exists for exactly one caller. Say so instead.

### Shared free functions move out of whoever owns them today

| new module | contents (from) |
|---|---|
| `handlers/_fields.py` | `title`, `abstract` (new) · `begin_date` (new), `end_date` (extension) · `transaction_amount`, `resource_comment`, `resolve_resource` (supplement) |
| `handlers/_allocations.py` | `account_is_active`, `effective_end_date`, `latest_allocation` (extension) · `account_for_resource`, `new_allocation_end_date` (supplement) · `clamp_start_to_commission` (new) · **the two create policies as named functions** — `create_from_action_dates`, `create_from_project_history` |

Naming the create policies is the specific thing that prevents a recurrence of the `auth`
bug: two call sites of one function cannot drift, where two copies of thirty lines can and
did.

`_get` collapses to one definition. `dispatch.py`, `extractors.py` and `roster.py` import
it rather than each keeping a copy — put it wherever it does not create a cycle
(`sam/xras/_wire.py` is a reasonable home; `errors.py` is not, it should stay
dependency-free).

---

## Six bugs to fix, each with a test

Write the failing test first for each — several of these are invisible from the outside.

1. **The adjustment `auth_at_panel_mtg` flag** (`adjustment.py:152`). The test that would
   have caught it compares Supplement's and Adjustment's created-allocation rows for the
   same panel-authorised input.
2. **`DispatchResult.warnings` is discarded** (`actions.py:222-241`). Where it should land
   is a schema question — see the companion doc's `warnings` column candidate. Until that
   is decided, at minimum it must reach the app log rather than vanish, and the test
   should pin that a defect-3 disagreement is observable somewhere.
3. **Transfer's `projcode` is discarded** on the manual arm. `handlers/transfer.py:76`
   sets `DispatchResult.projcode`; `actions.py:237` calls `_finish(log_id,
   status='manual')` and drops it.
4. **Dead imports**: `resolve_allocation_type` (`supplement.py:47`), `Optional`
   (`adjustment.py:42`), `Tuple` (`new.py:39`).
5. **`update._plan_resource` takes an unused `action` param** (`update.py:143`) — the only
   references to the token are in comments at `:155` and `:171`.
6. **`XrasProjectCreationFailed` is defined below the function that raises it**
   (`new.py:282`, raised at `:216` and `:222`). Move it up, or into `errors.py` alongside
   `XrasActionRejected` — it is a sibling exception type.

---

## The constraint

**Behaviour-preserving except for those six.** The 5,213 existing tests are the safety
net, and they must stay green **with no assertion changes beyond the six**. If a test
needs rewriting to accommodate the refactor, that is a signal the refactor changed
behaviour — stop and find out why.

The five per-handler test modules each collapse their `management_transaction` patch to
the single seam in `base.py`. `tests/unit/test_xras_oracle.py:70-94` loses its five-module
loop and its explanatory docstring becomes a one-liner.

⚠️ **Re-run the leak check after every test run during this work**, because the refactor
moves the very seam that prevents it:

```sql
SELECT COUNT(*) FROM allocation_transaction WHERE DATE(creation_time) = CURDATE();
-- expect 0
```

---

## Why the order matters, weakly

Doing this before the stress work means the stress harness has **one** patch point rather
than five. That matters because the harness is exactly the kind of code where a missed
patch site fails silently — it writes to the shared database and the test still passes.

But it is a preference, not a dependency. If the DBA ticket cannot wait, do
[`XRAS_STRESS_AND_SCHEMA.md`](XRAS_STRESS_AND_SCHEMA.md) first and patch five modules.

---

## Definition of done

1. `pytest -q` green at ≥ 5,213, with no assertion changes beyond the six bugs.
2. `management_transaction` has **one** import site in `src/sam/xras/`.
3. `_get` has **one** definition; `_mark_panel_authorised` has one.
4. **No handler module imports from another handler module.**
5. Each of the six bugs has a test that fails before its fix.
6. The two create policies are named functions with two call sites each.
7. The leak check returns 0.
8. A `## Deviations` section in this file, per the house convention — this document is
   input, not contract.

---

## Deviations

All eight done. `management_transaction` has one import site, `get_field` and
`mark_panel_authorised` have one definition each, no handler module imports another, both
create policies are named with two call sites each, each of the six bugs had a test that
failed first, and the leak check returns 0.

### The measurements were wrong more often than the structure

Every structural claim held. A third of the numbers did not — verified against the tree
before any code moved.

| Doc said | Actually |
|---|---|
| `update.py` imports 15 symbols, **9 underscore-private** | 15 confirmed (14 module-level + `_plan_contracts` in a function body); **4** are underscore-prefixed. By the stricter "not in the source module's `__all__`" reading it is 7. Neither is 9 |
| **4 identical** `_get` definitions | 4 definitions, **3 byte-identical**; `extractors.py`'s was a textual variant with a different parameter name |
| **3 byte-identical** `_mark_panel_authorised` | bodies byte-identical (md5 `d3432f80…`); **signatures and docstrings all differed** |
| the projcode-strip idiom, **9 occurrences** | **7** exact; 8 counting `dispatch.py`'s differently-named binding; 9 counting transfer's inline |
| `create_allocation(...)` **9-kwarg** call ×4 | **7 kwargs**. Adjustment alone discarded the return value — which is the mechanical cause of bug 1 |
| `supplement._plan` vs `adjustment._plan`, **3 differences** | **4** behavioural + 2 cosmetic. `adjustment.py`'s own docstring also said three; both were stale |
| the create policies are "the same six lines modulo a variable name" | **4** shared lines. The names are *swapped* — `start` means different things in the two files — and the terminator differs (`continue` vs `return []`) |

None of these changed what to do. They are recorded because this doc asked to be
re-verified rather than trusted, and the exercise paid for itself twice below.

### Bug 1 was narrower than described, and the doc nearly broke a correct test

The doc said Adjustment "silently never gets `auth_at_panel_mtg` set" where Supplement
does. That is true **only of the create branch**.

`auth_at_panel_mtg` splits by *command*, not by handler.
`buildAdjustAllocationCommand` never sets it, so an ADJUSTMENT row is correctly bare —
and `tests/unit/test_xras_adjustment_handler.py` already pinned that, with a docstring
saying "the difference is the Java's, not a slip". It was right and is untouched.
`buildAddAllocationCommand` — the copy taken verbatim from the supplement factory — does
set it, and *that* branch was dropping the flag.

Acting on the doc's wording would have "fixed" a correct test. The narrower bug is still
real: `auth` was computed, threaded through the creations tuple, unpacked and discarded.

It was invisible because every Adjustment test used the default `allocationType='Small'`,
which is not panel-authorised, so the flag was `False` either way. The fix ships with a
**pair** of tests — `'Large'` (→ CHAP) and the default — so the flag is proven to track
the type rather than the branch.

### Bug 6 took the doc's other option, for a reason the doc did not know

The doc offered "move it up, or into `errors.py` alongside `XrasActionRejected` — it is a
sibling exception type". **`errors.py` would have failed two gates.**
`test_xras_errors.py` and `test_xras_error_coverage.py` both enumerate that module's
public callables to prove every error-string builder is exported and declared, excluding
`ActionErrors` and `XrasActionRejected` by name. A class is callable, so
`XrasProjectCreationFailed` would have joined the 34-builder matrix. It moved up within
`new.py`, and the class docstring now says why.

### An ordering hazard the doc did not contain

The doc proposed `panel_authorised` as a `cached_property`. That is unsafe.

`auth_at_panel_meeting`'s second arm reads `project.allocation_type`, and Update
**writes** that column through `project.update()`, which flushes. Under lazy evaluation
Update's ADD branch would be the first touch — after the flush — so it would read back the
allocation type the action had just installed rather than the one the project had on
arrival. Nothing in the suite would have caught it: no test changes `allocationType` on an
Update whose resources take the add branch.

It is a plain attribute, assigned as an early line of each `assemble()`. Four visible
assignments beat one invisible ordering dependency, and the constraint is documented on
both `ActionHandler` and `auth_at_panel_meeting`.

Two smaller hooks in the same family, both noted in the base class:

- **Extension's "changed nothing" log fires after the commit** and must keep doing so.
  Folding it into `execute()` would let a run that then failed to commit still claim it
  completed. It moved to a `result()` override, which runs at the same point.
- **`ActionHandler.project` returns `None` rather than raising** when the row is absent.
  Supplement and Adjustment both reach a silent `processed` no-op through that arm;
  raising would have converted an unreachable no-op into a 500.

### `NewHandler.project` needed a name of its own

`project` means *the existing project named by `requestNumber`*, which for New is always
`None` by dispatch invariant. The created row lives on `created_project`.

This is not tidiness. `self.project = created` is legal and tempting, and it would flip
`auth_at_panel_meeting`'s second arm from "no project, so `False`" to "the type this
action just assigned" — on the handler with the highest failure rate and the least
production evidence. `TestTheNewHandlerDoesNotOwnProject` is the tripwire.

### The extraction landed in two commits, not one

`bb47149` moves the bodies and leaves `# noqa: F401` re-exports in the old modules, so
**845 tests pass with zero test edits**. `6af9b98` repoints the imports and drops the
shims.

The point is evidential. A move that requires editing the tests that verify it has spent
its own safety net; a move the untouched suite accepts is proof. Worth repeating for any
future extraction here.

### The seam guard is a runtime scan, not a grep

DoD item 2 said "one import site". `tests/unit/test_xras_transaction_seam.py` enforces it
by scanning `vars(module)` across `sam.xras` at runtime — deliberately not a grep (blind
to a re-export) and not an AST walk (blind to a runtime rebind), since a re-export *is* a
module global. Verified to fail by reinstating the import in `extension.py`.

It carries a second, stronger test: patch only `base`, turn `session.commit` into a
failure, and drive real actions through `dispatch_action`. That one does not care how the
code reached a commit. It also pins that a **rejected** action opens no transaction at
all — the property that lets the 422 promise nothing was written.

### Also fixed, being adjacent

- `_fit()` now guards `projcode_result`, because bug 3 made that path live. The other
  three `actions.py` hardening items stay with
  [`XRAS_STRESS_AND_SCHEMA.md`](XRAS_STRESS_AND_SCHEMA.md).
- Two stale docstrings: `adjustment.py`'s module header claimed "the absence of
  `auth_at_panel_mtg`", and `_plan`'s argued against sharing itself on a count of
  differences that was itself wrong.

### Explicitly not done

- **`DispatchResult.warnings` still has no column.** It now reaches the app log against
  `log_id`, which is the handle an operator has. Where it ultimately belongs is
  C.1b's `warnings` column candidate, and this deliberately does not pre-empt it.
- **The duplicate `Project.get_by_projcode` in `dispatch.select_service`** stays. The
  cached property collapses Supplement's three in-handler lookups to one, but the
  dispatcher resolves the same row first and handing it over would change `register()`'s
  signature — churn for one query.
- **`supplement`'s create branch still has no positivity gate** where Adjustment's does.
  Pulling Adjustment's guard into the shared policy would turn a Supplement crash into a
  422 — arguably better, but not one of the six.
