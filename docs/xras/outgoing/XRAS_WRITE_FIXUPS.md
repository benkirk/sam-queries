# XRAS write fixups — resolving an erroneously-reconciled placeholder

**Status: research + live-proven capability, NOT built.** This is a handoff
document. It records what the outbound XRAS credential can and cannot *write*,
proven against production on 2026-08-20, and designs one prototypical fixup —
clearing a wrongly-reconciled ARC placeholder off the **Pending Users**
card — so a future session can build it without re-probing a
destructive API. The direction is the same as
[`XRAS_OUTGOING_QUERIES.md`](XRAS_OUTGOING_QUERIES.md): SAM calling *out* to
`https://api.xras.org/v1/…`. Everything here is the **write** half that document
deliberately left closed.

> ⚠️ **Nothing in this document has a write client behind it yet.** The shipped
> `XrasApiClient` is GET-only *by construction* — its only transport primitive is
> `_get`, and `tests/unit/test_xras_api_client.py` pins that no
> post/put/patch/delete callable exists on the class. Building any of this means
> a **new** client with its own credential and its own review
> ([`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../PROJECT_AND_ACCOUNT_LIFECYCLE.md) § 4),
> never a relaxation of that one.

---

## 1. The problem this targets

The **Pending Users** tab
(`/allocations/xras`, `xras_accounts_fragment`) lists people who must exist and
be active in SAM before an approved XRAS action can be applied. A row is an ARC
**placeholder** — the `<name>-user-<token>` username XRAS mints for a researcher
with no site account — when `classify_accounts` finds no active `users` row for
that username.

Most placeholders on that card are *correct*: they are genuinely unreconciled
people (`isReconciled = false`), and the row is the true signal *create this
account*. That is the healthy path, and this fixup must not touch it.

**The failure mode is a placeholder that is `isReconciled = true` yet still on
the card.** That combination is contradictory, and it is the tell of bad data:

- A *properly* reconciled placeholder does not exist anymore. Reconciliation in
  XRAS **is a merge** (§ 3) — the placeholder is folded into the real identity
  and **deleted** — after which XRAS sends the real username on the wire and the
  placeholder never reaches our card.
- So a placeholder that both **resolves** (`GET /v1/people/<placeholder>` → 200,
  not 404) **and** carries `isReconciled = true` was flagged reconciled
  *without* a merge. The documented cause is on the API: `POST /v1/people`
  records `isReconciled` as *"(default **true**) — only used when creating the
  user in XRAS"*, so any ARC-side creation path that omits the flag mints the
  placeholder already-reconciled. It then never appears on XRAS's own
  Unreconciled Users queue, so no operator ever merges it, so XRAS keeps sending
  the placeholder username, so the SAM handoff stays blocked forever.

This is the concrete "reactivate `bjsmith`"-style false nag, one layer up: the
card says *create `mding-user-efmlx`* when the person already has an active SAM
account and an active real XRAS identity. Nobody can act on it from SAM today.

### The trigger predicate

> A row qualifies for the fixup when **`placeholder` is true AND
> `is_reconciled` is true**. Both are already computed and already on the
> `VIEW_XRAS` side of the PII line (`is_reconciled` is "account state, not a
> personal detail" — `xras_accounts_fragment` in `xras/card_routes.py`). The control itself is gated higher,
> on **`MANAGE_XRAS`** (§ 5).

Note the three-way split the card already half-recognizes (see the badge comment
in `xras_accounts_card.html` — *"placeholder", NOT "unreconciled"*):

| placeholder | `is_reconciled` | Meaning | Action |
|---|---|---|---|
| true | **false** | genuinely unreconciled — XRAS can't say who they are | create the SAM account (healthy path, leave alone) |
| true | **true** | ⚠️ erroneously reconciled — merge never happened | **this fixup** |
| true | **None** (404 on enrich) | already merged away in XRAS; row is a stale echo of the old username | ages out as real-username actions arrive |

---

## 2. What the credential can actually write — proven live 2026-08-20

Probed against production with the existing `XRAS_API_KEY`, each write approved
by the operator. The authorization signal is **401 vs 4xx**: 401 = the key is
not provisioned for that route; a 404/422 = the route accepted us and only the
*target* was wrong (so nothing was written, but the capability is real).

| Operation | Endpoint | Result | Verdict |
|---|---|---|---|
| **Merge** one person into another | `POST /v1/people/<u>/merge/<new_u>` | 404 on nonexistent users (arcguest); **200 on the real demo** | ✅ **authorized, user-agnostic** |
| **Un-flag** reconciliation | `POST /v1/people/<u>` with `isReconciled=false` | **200 but the flag is IGNORED** — re-GET still `true` | ❌ **impossible** — documented create-only |
| **Withdraw** a submitted action | `DELETE /v1/requests/<id>/actions/<aid>/submit` | 404 as the PI (`msmart`), **401 as arcguest**; **200 on real demos** | ✅ authorized **but strictly `XA-USER`-scoped** to a role-holder |
| **Delete** a whole request | `DELETE /v1/requests/<id>` | **401 for every `XA-USER`** | ❌ **not provisioned** |
| **Re-submit** an action | `POST /v1/requests/<id>/actions/<aid>/submit` | **200** as a role-holder; → `Under Review` | ✅ authorized, `XA-USER`-scoped |
| **Validate** an action (read) | `GET …/actions/<aid>/validate` | **200** as a role-holder | ✅ ⚠️ the verdict depends on *who* you impersonate |
| **Add / remove** a role | `POST /v1/requests/<id>/roles/<roleType>/<u>`, `DELETE …/roles/<roleId>` | **200** as a role-holder | ✅ `roleType` is the **string** here, not the id |
| **Request detail** / `rules{}` | `GET /v1/requests/<id>` | **401** in every context, for every `XA-USER` | ❌ **not provisioned** — the legal-moves read is closed |

The last four rows were settled on **2026-08-21** by the targeted probe in
[`XRAS_WRITE_PROBES.md`](XRAS_WRITE_PROBES.md), which also records the net-zero proof, the
role-type encoding trap, and why `rules{}` being closed changes the UI design. Read it before
adding any verb to the write client.

Two facts do the most work here:

1. **You cannot "unreconcile."** The obvious button — flip `isReconciled` back to
   false so the placeholder rejoins XRAS's Unreconciled queue and the normal flow
   finishes — **does not exist**. The endpoint returns 200 and silently drops the
   parameter. A UI action wired to it would be the worst kind: green, and inert.
   The XRAS admin app agrees — a reconciled person's `/people/unreconciled/<id>`
   page **404s**, and the person page offers no un-reconcile control.
2. **Merge is the only real remedy, and it is destructive.** `merge` **deletes**
   the placeholder (the API doc is explicit: *"This account will be deleted"*)
   and folds its roles into the retained target. It is roughly reversible only by
   re-creating and re-roling by hand — treat it as one-way.

⚠️ **Merge does not copy person detail.** After the demo merge of
`gsaha-user-hv1bu` → `gouravsaha`, the target still had `phone: null`,
`residenceCountry: null`, and lost the placeholder's richer `organization`
string. Whatever a future account-creation step needs off the placeholder
(`residenceCountry` especially, which the inbound wire never carries) must be
**captured before** the merge — the worklist enrichment already caches exactly
those fields.

---

## 3. Why the fixup is a **merge**, named honestly

The operator's instinct is "unreconcile this bad row." The capability reality
turns that into: **merge the placeholder into the correct real identity** — which
is what a *correct* reconciliation would have done in the first place. So the
control resolves the error by completing the merge that never happened, not by
reverting a flag that cannot be reverted.

That reframing must reach the UI. A button labeled "Unreconcile" would promise
the one thing the API refuses. Name it for what it does — **"Resolve identity
(merge in XRAS)"** or similar — and let a popover carry the mechanics:

> **Resolve identity in XRAS.** This placeholder is marked *reconciled* in XRAS
> but was never merged into a real account, so XRAS keeps sending this throwaway
> username and the handoff stays blocked. Resolving it **merges this placeholder
> into the real XRAS identity you pick below and deletes the placeholder.** From
> then on XRAS sends the real username. This does **not** create a SAM account —
> that is still a separate step if the person has none. It cannot be undone from
> here.

---

## 4. The three dev cases — the decision tree, from real data

The development DB carries three of these (ingested bad data). They are not three
copies of one case; they are the whole decision tree, and the third is why this
cannot be a one-click auto-merge.

**Case A — `gsaha-user-hv1bu` — one obvious target (already fixed).**
Placeholder resolved `isReconciled=true`, email `gs27@iitbbs.ac.in`. Exactly one
real identity shared that email — `gouravsaha`, active in SAM. Merged
placeholder → `gouravsaha` in the 2026-08-20 demo; the placeholder now 404s and
its roles moved. This is the clean single-candidate path.

**Case B — `mding-user-efmlx` — one obvious target (ready to fix).**
Placeholder `isReconciled=true`, Mingze Ding, `mding1@bu.edu`. The real XRAS
account `mding` **coexists** with the same name and email and is `isReconciled=true` — the coexistence is itself the proof no merge ever happened.
SAM `mding` is active. Single email-exact candidate → merge
`mding-user-efmlx` → `mding`.

**Case C — `kquagraine-user-89o84` — TWO candidates, must disambiguate. ⚠️**
Placeholder `isReconciled=true`, Kwesi Quagraine, `ktquagra@tamu.edu`, Texas
A&M. Searching XRAS on the surname returns **two** real identities, both active
in SAM, both named Kwesi Quagraine:

| username | email | organization | is it the target? |
|---|---|---|---|
| `ktquagra` | `ktquagra@tamu.edu` | TEXAS A & M UNIVERSITY | ✅ **yes** — email and org match the placeholder exactly |
| `kwesiq` | `kay.quagraine@gmail.com` | NCAR/EDECD | ❌ decoy — same human's *NCAR-staff* persona, different email/org |

A name-only match picks arbitrarily, and merge is destructive, so picking
`kwesiq` would fold a TAMU request's roles into an NCAR-staff account — a real,
unrecoverable error. **The safe key is the email/org match, not the name.** This
case is the reason the feature is an *assisted operator decision* that shows the
candidate detail sheets — exactly what the XRAS admin Reconcile-User screen does —
and never an automation that guesses the target.

Decision tree the control should implement:

```
row is (placeholder AND is_reconciled)
        │
        ├─ search XRAS + SAM for the real identity (by email first, then org, then name)
        │
        ├─ exactly one email-exact active candidate ──► offer merge into it (confirm)
        ├─ several candidates ───────────────────────► show all with email+org; operator picks; NO default
        └─ no candidate at all ──────────────────────► merge is impossible; the person genuinely
                                                        needs an account/identity created first
                                                        (this is the ordinary "create" path — the
                                                        reconciled flag was a red herring)
```

---

## 5. Proposed UX — a `MANAGE_XRAS` fixup on the card

- **Where.** A per-row action on `xras_accounts_card.html`, visible only when
  `may_manage` (MANAGE_XRAS) **and** the row is `placeholder and is_reconciled`.
  It sits beside the existing person-detail disclosure, not on the `<tr>` (the
  Bootstrap collapse data-api runs in capture phase — the card's own comment
  explains why row-level controls misfire).
- **Popover** carries the § 3 copy. This is a destructive, cross-system write; the
  operator must see what it does *before* the modal.
- **Modal** = the assisted decision (§ 4): the placeholder's detail sheet on one
  side, candidate real identities (XRAS `search/people` ∩ SAM `users`, ranked
  email → org → name) on the other, each with email and organization shown. No
  pre-selected default when there is more than one. Mirror the XRAS admin
  Reconcile-User screen deliberately — operators who know that screen will read
  this one for free.
- **On confirm**, the new write client calls `merge`, then **re-GETs the
  placeholder to confirm it 404s** (§ 2 gotcha: never trust the 200), captures
  the pre-merge person detail into the audit row, and fires the standard
  close-modal + reload trigger. Classification is a live check, so the row leaves
  the card on the next render with nothing to mark done.
- **Route protection** follows the card: `@require_permission(Permission.MANAGE_XRAS)`.
  MANAGE_XRAS is the strong tier here (`rbac.py:171-172`, `:261`), the same gate
  that already unlocks PII on this card.

---

## 6. The write client — the structural rules any build must keep

1. **A new class, not a verb on `XrasApiClient`.** e.g.
   `sam.integration.xras_api.admin_client.XrasAdminClient`, its own module, its
   own tests. The GET-only pin on the read client stays.
2. **Its own credential, fail-closed.** A separate lever
   (`XRAS_WRITE_ENABLED`, default off, set explicitly in `helm/values.yaml`) and,
   ideally, a **separately-scoped key** — today one key can read reports *and*
   merge people *and* withdraw actions, which is far too much authority for the
   reporting path to also hold. Raise a scoped-write-key request with XRAS; it
   pairs with the key-rotation ask already on the outgoing deferred register.
3. **Verify after every write.** `merge` and `POST /v1/people` both return 200 on
   a no-op (the `isReconciled` finding is the proof). A write is not done until a
   read confirms it. Bake this into the client, not the caller.
4. **Audit every write to a table.** Username(s), operator, timestamp, the
   captured pre-merge person detail, the target chosen, and the verify result.
   This is the same follow-on storage the read-side worklist deferred
   (`xras_account_event`, `XRAS_OUTGOING_QUERIES.md` § 7.6) — a merge is exactly
   the "operator did an irreversible thing to this username" event that table was
   shaped for.
5. **No Click/Flask/rich/kubernetes under `src/sam/`** (AST-gated), same as the
   read client.

---

## 7. Design tensions to reconcile when building

- **`enrich_worklist`'s "reconciled = easy" framing is half-right and half the
  bug.** Its docstring calls a reconciled placeholder *"the easy case — there is
  a real detail sheet behind it to create the account from."* True for account
  *creation*, but it obscures the exact pathology this fixup targets: a reconciled
  placeholder still on the card is a *data error*, not an easy win. When the
  control lands, that docstring and the card copy should distinguish "reconciled
  and mergeable" from "reconciled but stuck."
- **The badge already refuses to conflate placeholder with unreconciled**
  (`xras_accounts_card.html`). Good — build on it. The new control keys on the
  *conjunction* the badge deliberately keeps separate.
- **Withdraw-action (§ 2) is a sibling capability, not part of this fixup** —
  fully characterized in the **Addendum**. It closes *stale requests* rather than
  fixing a person, shares the write client and audit table, and is worth building
  in the same effort, but it is a *request* operation impersonating the PI whereas
  this fixup is a *person* operation. Keep them distinct in the UI.

---

## 8. Open questions

1. **Scoped write key.** Will XRAS issue a credential that can `merge`/withdraw
   but not the rest of the write surface? Everything in § 6.2 is cleaner if yes.
2. **Candidate ranking.** Email-exact is the strong signal (Cases A/B/C all turn
   on it). Is email ever *legitimately* shared across two identities in the NCAR
   process such that email-exact still needs org to disambiguate? Case C's decoy
   differs on email, so email alone would have sufficed there — but the modal
   should still show org, and never auto-merge on a multi-candidate result.
3. **Post-merge account creation.** After a merge into a real identity that has an
   active SAM account (Cases A/B), the handoff can proceed. When the real identity
   exists in XRAS but **not** in SAM, merge fixes the wire username but the person
   still needs a SAM account — the row re-classifies from "erroneously reconciled
   placeholder" to an ordinary "create" and stays on the card, correctly. Confirm
   that is the desired behavior (it is: SAM never creates users).

---

## 9. References

| | |
|---|---|
| [`XRAS_OUTGOING_QUERIES.md`](XRAS_OUTGOING_QUERIES.md) | The read-side worklist and the full readable/closed API surface. § 4.7 first recorded the write surface this document exercises |
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../PROJECT_AND_ACCOUNT_LIFECYCLE.md) | § 2 *SAM never creates users*; § 4 the write-direction ground rule and the two follow-ons (this is the first) |
| `src/sam/queries/xras_accounts.py` | `classify_accounts`, `enrich_worklist`, the trigger fields (`placeholder`, `is_reconciled`) |
| `src/webapp/dashboards/allocations/xras/card_routes.py` | `xras_accounts_fragment` — the PII gate and `may_manage` pattern the control extends |
| `src/webapp/templates/dashboards/allocations/partials/xras_accounts_card.html` | The card; the "placeholder ≠ unreconciled" badge nuance to build on |
| `src/sam/integration/xras_api/client.py` | The GET-only read client; the structural pin the write client must not weaken |
| Memory: `xras-write-capability` | The live-probe results, condensed |

**Live-proof provenance.** All § 2 results are from production `api.xras.org`,
2026-08-20, operator-approved. Real fixes applied that day: merged
`gsaha-user-hv1bu` → `gouravsaha`; withdrew the stale Supplement on NCAR0007 and
de-approved NCISL0000 / NCAR0006 (BJ Smith 2015 test requests). `mding-user-efmlx`
and `kquagraine-user-89o84` were **left in place** in production as standing test
fixtures for this feature — do not merge them without re-confirming they are still
wanted as test data.

---

## Addendum — the sibling capability: closing stale **Approved** requests (withdraw → Incomplete)

**A different use case from the rest of this document, and an equally valuable
finding from the 2026-08-20 session.** The body above fixes a *person* (a
mis-flagged placeholder). This addendum is about closing a *request* — the
"closing abandoned requests" follow-on named in
[`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../PROJECT_AND_ACCOUNT_LIFECYCLE.md) § 4 — using
the one write primitive the credential holds for it: **action withdrawal**. It
shares the write client, credential, and audit table (§ 6), so it is worth
building alongside the merge fixup, but it is a distinct operator action on a
*Pending request* row (it closes a request), not on a *Received push* row.

### The problem it closes

Requests approved years ago and never pushed still surface — as
`status=Approved` rows in `GET /v1/reports/requests`, which feed the *Pending
request* rows of **Pending Users**, and whose rosters can put a long-departed PI
there as a phantom "reactivate" nag. The person is not the problem; the stale
approval is. Measured tell from the session: PI `bjsmith` is **`active=0` in
SAM**, and that inactivity is the *only* reason the handoff surface demanded a
reactivation — the nag is driven purely by XRAS `status=Approved`, with **zero
dependence on SAM user state**. Reactivating `bjsmith` would be fixing the wrong
thing.

### What withdrawal does — measured, not inferred

`DELETE /v1/requests/<id>/actions/<aid>/submit` **un-submits one action back to
`Incomplete`** (its review `states[]` clear). Three demos, all operator-approved,
none a SAM project:

| Request | Before | Withdrew | After |
|---|---|---|---|
| **NCAR0007** (req 1167091) | Approved; New action *Approved* **+** Supplement action *Under Review* | the Supplement (30578), as PI `msmart` | Supplement → **Incomplete**; New stays *Approved*; **request stays Approved** |
| **NCISL0000** (req 1167142) | Approved; single New action *Approved* | the New (30654), as PI `bjsmith` | action → **Incomplete**; **request → Incomplete** |
| **NCAR0006** (req 1167089) | Approved; single New action *Approved* | the New (30347), as PI `bjsmith` | action → **Incomplete**; **request → Incomplete** |

Three findings, each load-bearing:

1. **Withdrawal works on an *already-Approved* action, not only an Under-Review
   one.** NCAR0007's stuck action was `Under Review`; NCISL0000/NCAR0006 were
   fully `Approved`. All three withdrew with a 200. So "de-approve an old award"
   is a real, available operation — not just "retract a pending submission."
2. **For a single-action request, withdrawing that action flips the whole
   `requestStatus` Approved → Incomplete** — which **removes it from
   `GET /v1/reports/requests?status=Approved`**, i.e. drops its *Pending request*
   row and takes it out of the sweep's dropped-push diff. That is the mechanism that
   actually "closes" the stale request from SAM's point of view.
3. **A multi-action request does not fully close this way.** NCAR0007 kept its
   `Approved` status because its legitimate New action survived — you withdraw
   each *stuck* action individually, and a genuinely-awarded sibling keeps the
   request alive. Correct behavior, but it means "close request" is really
   "withdraw the abandoned action(s)", per action.

### Semantics and limits — read before building

- ⚠️ **Withdraw is de-approval, NOT archival or deletion.** It reverts the award
  to a **draft** (`Incomplete`). It is reversible (the PI can resubmit), and it
  **rewrites the XRAS record** from "was Approved" to "Incomplete" — the history
  no longer shows an approval. Fine for junk 2015 test requests; for a real
  operator tool this is heavier than the word "close" implies, and the popover
  must say so.
- **Whole-request deletion is unavailable** (`DELETE /v1/requests/<id>` → 401 for
  every `XA-USER`, § 2). "Make it disappear entirely" is not on the table with
  this key; "de-approve so it stops surfacing" is. Anything stronger needs a
  broader XRAS grant or an upstream filter on their side.
- **Strictly `XA-USER`-scoped.** Withdrawal authorizes only when `XA-USER` is a
  real role-holder on the request (`msmart`/`bjsmith` → authorized; `arcguest` →
  401). A tool must set `XA-USER` to the request's **actual PI** per request —
  the same impersonation ARC itself uses, and a strong argument for logging every
  such action against the operator who triggered it, not the PI it ran as.

### Trigger and UX sketch

- **Where.** A *Pending request* row on the **Pending Users** tab
  (`xras_accounts_card.html`) — this operates on requests. Gate on `MANAGE_XRAS`.
- **Trigger.** An `Approved` request that is stale by policy — e.g. approved > N
  years, no matching `project.projcode` (the sweep's dropped-push signal already
  computes this), and/or all resources decommissioned, and/or PI inactive/departed.
  Surface these as a distinct "stale / abandoned" facet rather than auto-acting.
- **Action.** Per request, list its non-terminal actions; the operator withdraws
  the abandoned one(s), impersonating the PI, behind a popover that states plainly:
  *"This de-approves the selected action back to a draft in XRAS. The request's
  pending row will drop off Pending Users. It can be resubmitted by the PI. It does
  not delete anything."* Then re-GET to confirm the status moved (§ 2: never trust the 200),
  and write the audit row (request number, action id, PI impersonated, operator,
  before/after status).

### Why capture this separately from the merge fixup

They look adjacent — both are `MANAGE_XRAS`, both need the write client — but they
answer opposite questions and must not be conflated in the UI:

| | Merge fixup (body) | Withdraw (this addendum) |
|---|---|---|
| Object | a **person** (placeholder) | a **request/action** |
| Row | a *Received push* row | a *Pending request* row |
| Primitive | `POST …/merge/…` (deletes placeholder) | `DELETE …/actions/…/submit` (de-approves) |
| Impersonation | none (user-agnostic) | **the request's PI**, required |
| Reversible | effectively no | yes (resubmit) |
| Fixes the nag by | giving XRAS the right username | removing the stale Approved row |

Sometimes the *same* phantom "reactivate X" row is caused by a stale request, not
a mis-flagged person — so an operator working Pending Users needs both tools
reachable, and needs to know which one the situation calls for. That is the one
place the two features touch.
