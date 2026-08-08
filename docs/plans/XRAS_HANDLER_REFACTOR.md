# XRAS handler refactor — the base class the six handlers should have shared

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
