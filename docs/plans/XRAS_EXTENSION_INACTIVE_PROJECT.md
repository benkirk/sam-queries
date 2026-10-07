# XRAS Extension on an inactive project

Incident record (UCLA0042, 2026-10-05), the decision taken, and one deferred idea.
Code: `src/sam/xras/handlers/extension.py`, `src/sam/xras/handlers/_allocations.py`
(`account_is_extendable`), `src/sam/xras/preflight.py`; tests in
`tests/unit/xras/test_xras_extension_handler.py`. Sprint record:
`docs/xras/incoming/implemented/XRAS_SPRINT_C.md` § *Extension*, finding 4.

## In plain terms

The project's computing time ran out on June 30 and nobody renewed it right away.
On October 3 SAM's monthly housekeeping switched the project off, as it does for
any project expired 90 days or more (58 projects that morning). Two days later XRAS
sent SAM an approved extension to December 31. SAM looked at the project, saw it
was switched off, and quietly did nothing — the extension code only touched
*active* projects — then reported success because nothing had gone wrong from its
point of view. The project appeared on the XRAS card as needing activation, an
operator switched it back on, and the result was an active project whose
allocations still said June 30. The old Java SAM has the same rule and would have
done the same thing; the difference is that its deactivation button was pressed by
hand, so whether it beat an incoming extension was down to luck.

## Trace

| When (MT) | What | Evidence |
|---|---|---|
| 2025-06-26 | Legacy Extension #328186 pushes Casper, Derecho and Campaign Store to 2026-06-30 | three `EXTENSION` rows, `XRAS Extension Request` |
| 2026-10-03 04:30 | `deactivate_expired_projects` deactivates 58 projects whose allocations ended 06-30 | `project.inactivate_time` census |
| 2026-10-05 16:09 | XRAS approves Extension #403831, `resources: []`, end 2026-12-31 | XRAS reports API |
| 2026-10-05 16:10 | SAM: `service=extend`, `processed`, 200, `warnings NULL`, zero rows written | `xras_action_log` 257 |
| 2026-10-07 16:13 | Operator activates from the card (`xras_activation_event` 221) | |

Root cause: `account_is_active()` required `project.is_active`, so every account was
filtered out, `targets == []`, and the handler's only trace was an INFO line reading
"0 account(s) already end 2026-12-31". Legacy's `Account.isActive(Date)` is
`project.isActive() && resource.isCommissioned(date) && !creationTime.after(date)`
and `ExtendProjectAllocationActionCommandsFactory.create()` streams through it, so an
empty command set is a legacy success too. Of every `processed` Extension since the
2026-08-24 cutover, this was the only zero-write row that was not an XRAS re-post of an
already-applied action.

## Decision (2026-10-07)

An approved Extension is authoritative for dates; activating the project is a human
gate the XRAS card already enforces; Supplement, Adjustment and Update already write
to an inactive project's accounts through the unfiltered `account_for_resource`. So:

- `account_is_extendable` keeps the commissioned-resource and not-deleted tests and
  drops `project.is_active`. `preflight.infer_applied` uses the same predicate.
- The handler stamps `PROJECT_INACTIVE_WARNING` on the row from `assemble()`, so the
  re-check path carries it too. The XRAS table shows a warning count in the Errors
  column; the details modal and `sam-admin xras` detail view list the text.
- A zero-target run logs "matched no extendable account", distinct from the
  "already end" no-op.

UCLA0042 itself is repaired by hand: Admin → Extend Allocations, *Active at*
2026-06-30, Casper / Derecho / Campaign Store → 2026-12-31 (needs PR #755 so the
Allocations tab can see the expired rows).

## Future opportunity: automatic reactivation

Not built. When a write action (Extension, Supplement, Adjustment) lands on a project
that `deactivate_expired_projects` switched off — as opposed to one a human
deactivated for cause — SAM could call `Project.reactivate()` in the same
`management_transaction` and record an `xras_activation_event` with
`created_by='xras:<service>'`, closing the gap the card currently asks a human to
close. The blocker is telling the two deactivation sources apart: `inactivate_time`
records *when*, not *who*. Options are a `created_by`-style stamp on the task's
writes, or reading the task ledger for a run whose `detail` names the projcode.
Either is a small change once the policy question — should an XRAS approval override
a deliberate deactivation? — is answered by NUSD.
