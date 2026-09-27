# XRAS identity merge — "ready to merge" for ARC placeholders

**Status: BUILT 2026-08-25 on `xras_incoming_triage` (PR #482), after the two
production merges that day (`glarouche-user-cj2nx`, `jhu-user-fo5ee`).** Record of
what was measured, what was decided, and where each piece lives.

## The gap

An ARC placeholder (`<name>-user-<token>`) blocks every handoff naming it. The
merge modal existed but was offered only for the *misidentified* contradiction
(`placeholder and is_reconciled`); its finder queried XRAS by email (dead) and
surname (capped); the Pending Users row's SAM cross-reference was keyed on the
placeholder username, so every row read "No SAM account". An operator had to
click into each row to learn a merge was possible.

## What was measured (cutover log, `XRAS_TRIAGE_WEEK.md`, 25th 11:18 / 13:45)

| Fact | Consequence |
|---|---|
| XRAS `GET /v1/people/<u>` proxies SAM's identity service: six SAM users with no XRAS roles all resolved, reconciled | A merge target exists the moment SAM has an active user with the email. **No create step, ever.** |
| `isReconciled` is never flipped: the retained identity is already `true`, the placeholder is deleted | Nothing to un-flag; "unidentified" is a badge, not a blocker |
| `search/people` matches name/username only, capped at 20 ("Hu" hides `jhu279`, "Jie Hu" finds it) | Ask SAM for the email holder directly; search the full name too |
| `email_address.email_address` is `utf8mb3_bin`, `users.username` is `general_ci` | Lower both sides of the email match — the opposite of the username trap |
| Of 7 placeholders on the board that day, 1 had a SAM account (email), 0 had an XRAS identity by search | The proactive signal's value is *when the account lands*: the row flips at render, no sweep |

## Design decisions

- **One derivation.** `sam_merge_targets(session, emails)` (`sam/queries/xras_accounts.py`)
  is the only place "does SAM hold this placeholder's email" is answered. Two
  *active* holders is ambiguous and yields no target (merge deletes the loser).
- **A caller-applied stamp**, `stamp_merge_targets(session, rows)`, after
  `enrich_worklist` (a Feed-A row has no email until then), at three sites: the card
  route, the CLI builder, the sweep. It sets `row['merge_target']`, re-derives
  `remedy` (`merge` / `reactivate` / `create`), backfills snapshot rows from an older
  image, and re-sorts with `worklist_sort_key` (received pushes, then the cheapest
  remedy, then username).
- **`remedy` is the "Needs" facet** (Ready to merge / New account / Reactivation);
  `classification` stays the classifier's own fact and in `counts`. The hidden filter
  form follows, and a test pins that every chip has a control there.
- **PII line.** The matched *username* rides top-level (survives the VIEW_XRAS strip);
  the email stays inside `person`. The strip and the CLI report are MANAGE-tier.
- **No preselect** in the merge modal; the exact match is rank 0 with the green badge.
- **The identity report is a pivot** over already-stamped rows — no second derivation,
  no network on the card (`validate=False`, inline persons + Feed B).

## Where it lives

| Piece | File |
|---|---|
| seam, stamp, sort key, counts | `src/sam/queries/xras_accounts.py` |
| sweep stamp (ledger `merge_ready` is sweep-time Feed B) | `src/scheduling/tasks/xras_sweep.py` |
| CLI `--accounts` (`merge_target`, "merge into") and `--identity-report` | `src/cli/xras/builders.py`, `commands.py`, `display.py`, `src/cli/cmds/admin.py` |
| Pending Users: stamp, facet, header count, Needs cell, expansion gate | `src/webapp/dashboards/allocations/xras/card_routes.py`, `_shared.py`, `templates/dashboards/allocations/partials/xras_accounts_card.html`, `templates/dashboards/allocations/xras.html` |
| user modal + request roster gates and copy | `src/webapp/dashboards/allocations/xras/modals.py`, `partials/xras_user_detail.html`, `partials/_xras_remediation_actions.html` |
| finder (direct `get_person` at rank 0, full name + surname) | `src/webapp/dashboards/allocations/xras/remediation.py`, `partials/xras_merge_form.html` |
| report, strip, popover | `src/sam/queries/xras_identity_report.py`, `remediation.py`, `partials/xras_remediations_card.html`, `templates/dashboards/fragments/glossary.html` |
| factory | `tests/factories/core.py` (`make_email_address`) |

## Traps for whoever touches it

- The derivation is imported **at call time** in every consumer so one patch point
  (`sam.queries.xras_accounts.sam_merge_targets`) serves the route tests, whose
  `db.session` cannot see SAVEPOINT rows.
- Stamp **after** enrichment. Inside `get_account_worklist` it would mark most Feed-A
  rows "no target".
- A merge-ready row is still `classification == 'absent'` in `users`; the placeholder
  username stays a non-link, the *target* links.
- The strip's per-target control is an htmx **button** opening `#auditDetailsModal` from
  the card body — `data-bs-toggle` is right there and wrong inside an open modal.
- The e2e file may not assert a username, email, or count; the strip test checks
  structure and that no `@` appears.

## Verification

Unit: `tests/unit/test_xras_accounts_query.py::TestMergeTargets`,
`test_xras_accounts_card.py::TestReadyToMerge`,
`test_xras_remediations.py::TestUnidentifiedPlaceholdersCanMerge` and
`::TestIdentityUnblockStrip`, `test_xras_identity_report.py`,
`test_admin_xras_cli.py::TestIdentityReportMode`, `test_task_xras_sweep.py`.
Local: re-sweep (`docker compose exec webdev sam-admin tasks --run xras_sweep --force`),
seed a local user + email for one placeholder, load Allocations → XRAS: the row sorts
first as "Ready to merge", the strip opens the merge modal with the exact match ranked
first and nothing preselected; `make e2e SAM_E2E_BASE_URL=http://localhost:5050`.
Prod: `sam-admin xras --identity-report` on a pod after the dispatch.

## Follow-ups

- The six "needs account" placeholders flip on their own once accounts exist; nothing
  to build. The report carries the email so the account request is copy-paste.
- `sam_merge_targets` could also serve `enrich_worklist`'s SAM cross-reference for
  non-placeholder rows if a real-username identity ever needs the same treatment.
