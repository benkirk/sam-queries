# How a project and its people reach SAM

**The context every other document in `docs/xras/` assumes and none of them states.**
The incoming reference explains the wire; the runbook explains cutover day; the triage
playbook explains a failed action. None of them says where a project *comes from*, who
creates the people on it, or why SAM cannot fix the most common failure itself.

---

## 1 · Two ways a project is born, and they are not symmetric

### The ARC → XRAS → SAM path — the majority, especially university

```
researcher                ACCESS / XRAS                     SAM
    │                          │                             │
    │  submits a request       │                             │
    ├─────────────────────────►│                             │
    │  arc.ucar.edu/           │  reviewed, iterated,        │
    │  xras_submit/            │  possibly revised           │
    │  opportunities           │        │                    │
    │                          │        ▼                    │
    │                          │    approved                 │
    │                          ├────────────────────────────►│  POST /api/xras/v1/actions
    │                          │                             │  → project + allocations
```

A researcher starts at **`arc.ucar.edu/xras_submit/opportunities`** — call it **ARC**.
ARC posts the submission into **XRAS**, where it is reviewed and may be iterated on. Only
once it is **approved** does it come back to us, as a POST to
`/api/xras/v1/actions`. Legacy SAM performed this until the 2026 cutover;
`src/sam/xras/` and `src/webapp/api/xras/` are the reimplementation.

We may reimplement the submission step here one day. Not today.

### The internal path — first-class, and it must always work

Staff create a project directly in the SAM webapp: **Admin → Projects → Create Project**
(`webapp/dashboards/admin/projects_routes.py`). It is not a lesser path and nothing in
the XRAS work may compromise it.

The two share `next_projcode()` and `GidAllocation.allocate_next_gid()` and nothing else.
⚠️ They implement the *same* "the lead must exist" rule twice and **do not agree**: the
internal path checks existence via `validate_fk_existence` and enforces *active* only in
the picker's search query, while `sam/xras/roster.py` enforces both in code and rejects
the action with a 422. A forged POST naming an inactive user's id passes the internal
path server-side. Recorded, not yet fixed.

---

## 2 · ⚠️ SAM never creates users

**This is the fact that explains the whole account worklist, and it is not visible
anywhere in the code — only in its absence.**

`users` is **mirrored into SAM from an organizational LDAP** by a process that lives
outside this repository. Enrollment — including 2FA — is an enterprise function performed
by another team. SAM is a **reader** of identity, not a source of it.

What that looks like in the tree, all of it checkable:

| | |
|---|---|
| INSERT into `users` anywhere in `src/` | **none** |
| `User.create()` | **does not exist** — alone among ~21 models that have one |
| `User.update()` | **does not exist** either |
| Anything writing `users.active` or `users.locked` | **nothing**, in the entire repo |
| The only column SAM ever writes | `primary_gid`, via `User.set_primary_gid()` |

The strongest in-tree evidence is an outage. `src/sam/core/users.py:50-56` records that
the **2026-08-10 identity-sync cutover** dropped `pdb_modified_time` and
`idms_sync_token` from production `users` in a DDL SAM neither wrote nor knew about;
every page 500'd for ~20 minutes, and the incident is now pinned as a contract in
`tests/api/test_health_endpoints.py`. `PDB` and `IDMS` are the upstream's names, surviving
as column prefixes. SAM was a passive victim of a table it does not own.

**LDAP appears in this repo only as a *downstream*** — `sam/queries/directory_access.py`
feeds a "downstream LDAP provisioner", and `sam/provisioning.py` compares what SAM
believes against what the host actually provisions. The reverse direction, LDAP → `users`,
has no code here at all. That asymmetry is easy to misread as "SAM owns identity".

### The consequence, and why the worklist exists

An approved XRAS request cannot be applied until **every person on it exists and is
active in SAM**. `roster.py` rejects the action otherwise, and a missing **PI** is fatal
(`PI %s is not in database` → 422) while a missing Allocation Manager is explicitly not.
Unreconciled ARC placeholder identities are, by this repo's own measurement
(`sam/xras/handlers/new.py:24-27`), **55% of production handoff failures** — the single
largest cause.

So the fix is **not code that creates accounts.** It is a worklist that tells the team
who does: *who*, *why*, and — behind `MANAGE_XRAS` — with what detail. That is
Allocations → XRAS → **Pending Users**, and it is why every remedy on that card names
an artifact ("New account", "Reactivation") rather than an action, and why the card
carries a banner saying SAM cannot perform either.

⚠️ **Reactivation is upstream too.** It is tempting to read "Reactivate" as something a
SAM admin does, because Flask-Admin's `UserAdmin` exposes `active`/`locked` and the
`nusd`/`csg` bundles hold `EDIT_USERS`. That surface is kill-switched off in production
(`FLASK_ADMIN_ENABLED`) and is not the sanctioned mechanism. Both remedies land upstream.

### The one thing SAM *can* do about it

**Notice early.** A row leaves the worklist by itself the moment the mirror catches up —
classification is a check against the current `users` table on every render, so there is
nothing to mark done. What SAM adds is lead time and a single place to look, instead of
one ticket per project.

---

## 3 · The two feeds, one tab

The **Pending Users** tab on Allocations → XRAS is person-keyed and answers *"who needs
an account"*. It is fed by two sources, unioned on the casefolded username, each carrying
a **Source** badge so a row says which feed put it there:

| | **Received push** | **Pending request** |
|---|---|---|
| Source | `xras_action_log` — our own audit table | `GET /v1/reports/requests` via `xras_sweep` |
| Means | precisely the actions that **have posted** | approved in XRAS, **may or may not** have posted |
| Availability | **always** | only while `XRAS_OUTGOING_ENABLED` is on *and* a sweep has published |

**Overlap between the feeds is normal**, and neither is a subset of the other. A received
push is the more urgent flavor — a push already arrived and is blocked — so those rows
sort first, and their count is the health metric for the proactive side: it trends toward
zero once the pending-request work lands ahead of the push. When the pending half is
unavailable (a lever off, no sweep) the card shows the received-push rows and a
degraded-half note says which state it is in, rather than a blank tab.

`sam-admin xras --accounts` shows the same union on the CLI and reports `pending_checked`
so a caller can tell "the pending feed was empty" from "the pending feed was unreadable".

⚠️ **The window pills mean "what showed up in the last N days"**, and that is
`received_time` for a received push and `submitDate` for a pending request — the same
question asked of two feeds that date themselves differently. Do not filter the pending
half on its period of performance; that was tried, and because a pending allocation almost
always ends a year out it keeps every row at every width and the control looks dead.

---

## 4 · Where this is going

`ARC → XRAS → SAM` is the direction that exists. Two follow-ons are recorded, both of
which need SAM to talk *back*:

- **Closing abandoned requests.** Requests approved years ago and never pushed still
  surface as people needing accounts. That is upstream data, not a SAM filter problem.
- **Buying lead time.** SAM currently sees these people at *approval*.
  `SAM_TASKS_XRAS_SWEEP_STATUS` is already a knob, and submissions are knowable weeks
  earlier.

⚠️ Both break a property that is currently **structural**, not conventional:
`XrasApiClient` has no verb method other than an internal `_get`, and a test asserts no
`post`/`put`/`patch`/`delete` callable exists on the class — because the same credential
can create requests, modify roles and *merge one person into another*. A write direction
is a new client with its own credential and its own review, never a relaxation of this
one.

Design and deferred work: [`../plans/XRAS_ACCOUNT_QUEUE.md`](../plans/XRAS_ACCOUNT_QUEUE.md).
